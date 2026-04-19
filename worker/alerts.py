"""
Alert worker — runs on a schedule (every 5 min during market hours).

For each active alert rule:
  1. Refresh the ticker's market data.
  2. Evaluate the rule against current price / RSI.
  3. If triggered: send email via Resend, log it, and (if one_shot) deactivate the rule.

Environment variables required for email:
  RESEND_API_KEY        (get a free one at resend.com — 3000 emails/month)
  ALERT_FROM_EMAIL      (must be on a domain you've verified, OR use
                         "onboarding@resend.dev" for testing/personal use)
  ALERT_TO_EMAIL        (your personal email address)

Run this locally with:
  python -m worker.alerts

On Render, this is deployed as a Cron Job scheduled every 5 minutes.
"""
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Make backend importable when run as a cron job
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import get_conn, init_schema
from backend.market_data import fetch_and_store_all
from backend.market_hours import market_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] worker: %(message)s",
)
log = logging.getLogger("worker")


# -------------------------------------------------------------------
# Email via Resend (simple HTTPS POST, no SDK required)
# -------------------------------------------------------------------

def _send_email(subject: str, body: str) -> tuple[bool, str | None]:
    """Send an email via Resend's REST API. Returns (ok, error_message)."""
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("ALERT_FROM_EMAIL", "onboarding@resend.dev")
    to_email = os.environ.get("ALERT_TO_EMAIL")

    if not api_key or not to_email:
        return False, "RESEND_API_KEY or ALERT_TO_EMAIL not configured"

    payload = {
        "from": f"StockTracker <{from_email}>",
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            log.info(f"Email sent: {raw[:200]}")
            return True, None
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        log.error(f"Resend HTTP {e.code}: {err_body}")
        return False, f"HTTP {e.code}: {err_body[:300]}"
    except Exception as e:
        log.error(f"Email failed: {e}")
        return False, str(e)


# -------------------------------------------------------------------
# Rule evaluation
# -------------------------------------------------------------------

def _evaluate(rule, stock) -> tuple[bool, float | None, str]:
    """
    Returns (triggered, actual_value, human_message).
    """
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
        # Threshold is signed: positive = move up that much, negative = move down
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
# Main loop
# -------------------------------------------------------------------

def run_once(force: bool = False):
    """Check all active alerts once. If force=False, skip outside market hours."""
    init_schema()

    status = market_status()
    # We still fire alerts during pre/post hours because price changes matter;
    # only skip when fully closed (overnight, weekends, holidays).
    if not force and status["status"] == "closed":
        log.info(f"Market fully closed ({status['status']}) — skipping check")
        return

    # Load active rules
    with get_conn() as conn:
        rules = conn.execute("""
            SELECT ar.*, s.ticker as s_ticker
            FROM alert_rules ar
            JOIN stocks s ON s.ticker = ar.ticker
            WHERE ar.active = 1
        """).fetchall()

    if not rules:
        log.info("No active alert rules")
        return

    tickers = sorted({r["ticker"] for r in rules})
    log.info(f"Refreshing {len(tickers)} tickers for {len(rules)} alert rules")
    fetch_and_store_all(tickers)

    # Re-read stock data after refresh
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


if __name__ == "__main__":
    force = "--force" in sys.argv
    run_once(force=force)
