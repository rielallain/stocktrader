"""
Cron worker — pings the web service's /api/check-alerts endpoint.

This is intentionally tiny as a cron. The actual alert-evaluation and
email-sending logic (run_once, _evaluate, _send_email) is also here so the
web service's /api/check-alerts endpoint can reuse it inline — that way we
have one codebase and two entry points:

  1. Cron entry (main): POSTs to the web service URL. Keeps cron stateless.
  2. run_once(): called by the web service, does the actual work against
     the shared SQLite database.

Why this architecture:
  Render's free tier doesn't let cron jobs share a persistent disk with the
  web service. So the cron can't directly read the database. Instead it
  asks the web service to do the check — the web service has the disk.

Environment variables:

  For the CRON SERVICE (minimal):
    WEB_SERVICE_URL       full https URL of the web service (no trailing slash)
                          e.g. https://stocktrader-u9n5.onrender.com
    ALERT_CHECK_SECRET    shared secret matching the one set on the web service

  For the WEB SERVICE (when run_once executes there):
    RESEND_API_KEY        for sending email
    ALERT_FROM_EMAIL      defaults to onboarding@resend.dev
    ALERT_TO_EMAIL        your email
    ALERT_CHECK_SECRET    same value as on the cron service

Usage:
  python -m worker.alerts                 # poke the web service
  python -m worker.alerts --force         # force, even outside market hours
  python -m worker.alerts --direct        # run inline (skips HTTP, requires DB access)
"""
import json
import logging
import os
import smtplib
import sys
from email.mime.text import MIMEText
import urllib.request
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] worker: %(message)s",
)
log = logging.getLogger("worker")


# -------------------------------------------------------------------
# Email via Resend
# -------------------------------------------------------------------

def _send_email(subject: str, body: str) -> tuple[bool, str | None]:
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("ALERT_FROM_EMAIL", "onboarding@resend.dev")
    to_email = os.environ.get("ALERT_TO_EMAIL")

    if not api_key or not to_email:
        return False, "RESEND_API_KEY or ALERT_TO_EMAIL not configured"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = f"StockTracker <{from_email}>"
    msg["To"] = to_email

    # Use SMTP instead of the HTTP API. Resend's HTTP API endpoint sits behind
    # Cloudflare, which blocks Render's egress IPs with error 1010. SMTP runs
    # on port 465 and bypasses Cloudflare entirely. Same API key, no new deps.
    try:
        with smtplib.SMTP_SSL("smtp.resend.com", 465, timeout=15) as smtp:
            smtp.login("resend", api_key)
            smtp.sendmail(from_email, [to_email], msg.as_string())
        log.info(f"Email sent via SMTP: {subject}")
        return True, None
    except Exception as e:
        log.error(f"Email failed: {e}")
        return False, str(e)


# -------------------------------------------------------------------
# Rule evaluation
# -------------------------------------------------------------------

def _evaluate(rule, stock) -> tuple[bool, float | None, str]:
    ticker = rule["ticker"]
    rtype = rule["rule_type"]
    thresh = rule["threshold"]
    price = stock["last_price"]
    endorsement = stock["endorsement_price"] or 0.0
    rsi = stock["rsi"]
    company = stock["company_name"] or ticker

    if rtype == "price_above":
        if price is None:
            return False, None, ""
        triggered = price >= thresh
        msg = f"{ticker} ({company}) crossed ABOVE ${thresh:,.2f} — now ${price:,.2f}"
        return triggered, price, msg

    if rtype == "price_below":
        if price is None:
            return False, None, ""
        triggered = price <= thresh
        msg = f"{ticker} ({company}) crossed BELOW ${thresh:,.2f} — now ${price:,.2f}"
        return triggered, price, msg

    if rtype == "pct_from_endorsement":
        if price is None or endorsement <= 0:
            return False, None, ""
        pct = (price - endorsement) / endorsement * 100
        if thresh >= 0:
            triggered = pct >= thresh
            direction = "UP"
        else:
            triggered = pct <= thresh
            direction = "DOWN"
        sign = "+" if pct >= 0 else ""
        msg = (f"{ticker} ({company}) moved {direction} {thresh:+.1f}% from endorsement "
               f"(${endorsement:,.2f} → ${price:,.2f}, {sign}{pct:.2f}%)")
        return triggered, pct, msg

    if rtype == "rsi_above":
        if rsi is None:
            return False, None, ""
        triggered = rsi >= thresh
        msg = f"{ticker} ({company}) RSI {rsi:.1f} — overbought (>{thresh:.0f})"
        return triggered, rsi, msg

    if rtype == "rsi_below":
        if rsi is None:
            return False, None, ""
        triggered = rsi <= thresh
        msg = f"{ticker} ({company}) RSI {rsi:.1f} — oversold (<{thresh:.0f})"
        return triggered, rsi, msg

    return False, None, f"Unknown rule_type: {rtype}"


# -------------------------------------------------------------------
# Inline run — called from the web service
# -------------------------------------------------------------------

def run_once(force: bool = False):
    """Check all active alerts once. If force=False, skip outside market hours."""
    # Lazy import so the cron entry point doesn't try to open the DB (it can't
    # — the cron service doesn't mount the disk).
    from backend.db import get_conn, init_schema
    from backend.market_data import fetch_and_store_all
    from backend.market_hours import market_status

    init_schema()

    status = market_status()
    if not force and status["status"] == "closed":
        log.info(f"Market fully closed ({status['status']}) — skipping check")
        return {"skipped": True, "reason": "market_closed", "fired": 0, "checked": 0}

    with get_conn() as conn:
        rules = conn.execute("""
            SELECT ar.*, s.ticker as s_ticker
            FROM alert_rules ar
            JOIN stocks s ON s.ticker = ar.ticker
            WHERE ar.active = 1
        """).fetchall()

    if not rules:
        log.info("No active alert rules")
        return {"skipped": False, "fired": 0, "checked": 0}

    tickers = sorted({r["ticker"] for r in rules})
    log.info(f"Refreshing {len(tickers)} tickers for {len(rules)} alert rules")
    fetch_and_store_all(tickers)

    with get_conn() as conn:
        placeholders = ",".join("?" * len(tickers))
        stock_rows = conn.execute(
            f"SELECT * FROM stocks WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
    stocks = {r["ticker"]: r for r in stock_rows}

    now_iso = datetime.now(timezone.utc).isoformat()
    fired = 0

    for rule in rules:
        stock = stocks.get(rule["ticker"])
        if stock is None:
            continue
        triggered, actual, msg = _evaluate(rule, stock)
        if not triggered:
            continue

        subject = f"StockTracker alert: {rule['ticker']}"
        ok, err = _send_email(subject, msg)

        with get_conn() as conn:
            conn.execute("""
                INSERT INTO alert_log (rule_id, ticker, rule_type, threshold,
                                       actual, message, sent_ok, error, fired_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (rule["id"], rule["ticker"], rule["rule_type"], rule["threshold"],
                  actual, msg, 1 if ok else 0, err, now_iso))
            conn.execute(
                "UPDATE alert_rules SET last_triggered_at = ? WHERE id = ?",
                (now_iso, rule["id"]),
            )
            if rule["one_shot"] and ok:
                conn.execute(
                    "UPDATE alert_rules SET active = 0 WHERE id = ?",
                    (rule["id"],),
                )
                log.info(f"Alert {rule['id']} fired and deactivated (one-shot)")

        fired += 1
        log.info(f"FIRED: {msg}")

    log.info(f"Done. {fired} alerts fired of {len(rules)} checked")
    return {"skipped": False, "fired": fired, "checked": len(rules)}


# -------------------------------------------------------------------
# Cron entry point — pokes the web service URL
# -------------------------------------------------------------------

def poke_web_service(force: bool = False) -> int:
    """HTTPS POST to /api/check-alerts on the web service. Returns exit code."""
    url = os.environ.get("WEB_SERVICE_URL")
    secret = os.environ.get("ALERT_CHECK_SECRET")

    if not url:
        log.error("WEB_SERVICE_URL not set")
        return 2
    if not secret:
        log.error("ALERT_CHECK_SECRET not set")
        return 2

    endpoint = f"{url.rstrip('/')}/api/check-alerts"
    if force:
        endpoint += "?force=1"

    req = urllib.request.Request(
        endpoint,
        data=b"",
        headers={
            "X-Alert-Secret": secret,
            "Content-Type": "application/json",
            # Render's edge uses Cloudflare-style bot detection that 403s
            # requests with the default Python urllib User-Agent (error 1010).
            # Send a normal-looking browser UA to bypass this.
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                          "Version/17.0 Safari/605.1.15 stocktracker-cron/1.0",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read().decode("utf-8")
            log.info(f"check-alerts response: HTTP {resp.status} — {body[:300]}")
            return 0
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        log.error(f"check-alerts HTTP {e.code}: {err_body[:300]}")
        return 1
    except Exception as e:
        log.error(f"check-alerts request failed: {e}")
        return 1


if __name__ == "__main__":
    force = "--force" in sys.argv
    direct = "--direct" in sys.argv
    if direct:
        result = run_once(force=force)
        log.info(f"Direct run result: {result}")
        sys.exit(0)
    sys.exit(poke_web_service(force=force))
