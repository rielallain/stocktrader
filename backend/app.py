"""
StockTracker Web - Flask application.

Serves the PWA frontend and exposes a REST API:
  GET  /api/market-status
  GET  /api/stocks                      -> all rows from stocks table
  POST /api/stocks                      -> add ticker (validates via yfinance)
  PATCH /api/stocks/<ticker>            -> update endorsement/target/alloc/date
  DELETE /api/stocks/<ticker>           -> remove ticker
  POST /api/stocks/<ticker>/move        -> {"target": "portfolio"|"watchlist"|"both"}
  POST /api/refresh                     -> force refresh of all tickers
  POST /api/refresh/<ticker>            -> force refresh of one ticker
  GET  /api/validate/<ticker>           -> validation without saving
  GET  /api/alerts                      -> all alert rules
  POST /api/alerts                      -> create alert rule
  PATCH /api/alerts/<id>                -> toggle/update rule
  DELETE /api/alerts/<id>               -> delete rule
  GET  /api/alerts/log                  -> recent fired alerts
"""
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from backend.db import get_conn, init_schema
from backend.market_data import fetch_and_store_all, validate_ticker, fetch_one
from backend.market_hours import market_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("app")

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = Flask(
    __name__,
    static_folder=str(FRONTEND_DIR / "static"),
    template_folder=str(FRONTEND_DIR / "templates"),
)

# Initialize schema on import (safe, idempotent)
init_schema()


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

ALLOWED_RULE_TYPES = {
    "price_above",
    "price_below",
    "pct_from_endorsement",
    "rsi_above",
    "rsi_below",
}


def _row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def _stock_to_api(row):
    """Shape a stocks-table row for the frontend. Computes derived fields."""
    d = _row_to_dict(row)
    current = d.get("last_price")
    previous = d.get("previous_close")
    endorsement = d.get("endorsement_price") or 0.0

    # Day change
    if current is not None and previous:
        d["day_dollar_change"] = current - previous
        d["day_percent_change"] = (current - previous) / previous * 100
    else:
        d["day_dollar_change"] = None
        d["day_percent_change"] = None

    # Endorsement P/L
    if current is not None and endorsement > 0:
        d["dollar_change"] = current - endorsement
        d["percent_change"] = (current - endorsement) / endorsement * 100
    else:
        d["dollar_change"] = None
        d["percent_change"] = None

    return d


# -------------------------------------------------------------------
# Frontend routes
# -------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR / "templates", "index.html")


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(FRONTEND_DIR / "static", "manifest.webmanifest")


@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(FRONTEND_DIR / "static", "service-worker.js")


@app.route("/icon-<size>.png")
def icon(size):
    # Allow 192 and 512 for PWA
    if size in ("192", "512"):
        return send_from_directory(FRONTEND_DIR / "static", f"icon-{size}.png")
    return "", 404


# -------------------------------------------------------------------
# Market status
# -------------------------------------------------------------------

@app.get("/api/market-status")
def api_market_status():
    return jsonify(market_status())


# -------------------------------------------------------------------
# Stocks
# -------------------------------------------------------------------

@app.get("/api/stocks")
def api_list_stocks():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM stocks ORDER BY ticker").fetchall()
    return jsonify([_stock_to_api(r) for r in rows])


@app.post("/api/stocks")
def api_add_stock():
    data = request.get_json(force=True)
    ticker = (data.get("ticker") or "").strip().upper()
    endorsement = float(data.get("endorsement_price") or 0.0)
    target = float(data.get("target_price") or 0.0)
    target_list = data.get("target_list", "both")  # portfolio | watchlist | both
    endorsement_date = data.get("endorsement_date")
    allocation = data.get("allocation")

    if not ticker:
        return jsonify({"error": "Ticker is required"}), 400
    if target_list not in ("portfolio", "watchlist", "both"):
        return jsonify({"error": "Invalid target_list"}), 400

    # Validate with yfinance before adding
    snapshot = validate_ticker(ticker)
    if snapshot is None:
        return jsonify({"error": f"Could not fetch data for '{ticker}'. Check the symbol."}), 400

    is_portfolio = 1 if target_list in ("portfolio", "both") else 0
    is_watchlist = 1 if target_list in ("watchlist", "both") else 0

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO stocks (
                ticker, endorsement_price, is_portfolio, is_watchlist,
                company_name, last_price, last_fetched,
                previous_close, volume, market_cap, high_52w, low_52w,
                rsi, sma_200_pct, target_price, endorsement_date, allocation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                endorsement_price = excluded.endorsement_price,
                is_portfolio      = excluded.is_portfolio | stocks.is_portfolio,
                is_watchlist      = excluded.is_watchlist | stocks.is_watchlist,
                company_name      = excluded.company_name,
                last_price        = excluded.last_price,
                last_fetched      = excluded.last_fetched,
                previous_close    = excluded.previous_close,
                volume            = excluded.volume,
                market_cap        = excluded.market_cap,
                high_52w          = excluded.high_52w,
                low_52w           = excluded.low_52w,
                rsi               = excluded.rsi,
                sma_200_pct       = excluded.sma_200_pct,
                target_price      = excluded.target_price,
                endorsement_date  = excluded.endorsement_date,
                allocation        = excluded.allocation
        """, (
            ticker, endorsement, is_portfolio, is_watchlist,
            snapshot["company_name"], snapshot["current_price"], snapshot["fetched_at"],
            snapshot["previous_close"], snapshot["volume"], snapshot["market_cap"],
            snapshot["high_52w"], snapshot["low_52w"],
            snapshot["rsi"], snapshot["sma_200_pct"], target, endorsement_date, allocation,
        ))
        row = conn.execute("SELECT * FROM stocks WHERE ticker = ?", (ticker,)).fetchone()

    return jsonify(_stock_to_api(row)), 201


@app.patch("/api/stocks/<ticker>")
def api_update_stock(ticker):
    ticker = ticker.upper()
    data = request.get_json(force=True)

    fields = {}
    for key in ("endorsement_price", "target_price", "endorsement_date", "allocation"):
        if key in data:
            fields[key] = data[key]

    if not fields:
        return jsonify({"error": "No valid fields to update"}), 400

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [ticker]

    with get_conn() as conn:
        cur = conn.execute(f"UPDATE stocks SET {set_clause} WHERE ticker = ?", values)
        if cur.rowcount == 0:
            return jsonify({"error": "Ticker not found"}), 404
        row = conn.execute("SELECT * FROM stocks WHERE ticker = ?", (ticker,)).fetchone()

    return jsonify(_stock_to_api(row))


@app.delete("/api/stocks/<ticker>")
def api_delete_stock(ticker):
    ticker = ticker.upper()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM stocks WHERE ticker = ?", (ticker,))
        if cur.rowcount == 0:
            return jsonify({"error": "Ticker not found"}), 404
    return jsonify({"ok": True})


@app.post("/api/stocks/<ticker>/move")
def api_move_stock(ticker):
    ticker = ticker.upper()
    target = (request.get_json(force=True).get("target") or "").lower()
    if target not in ("portfolio", "watchlist", "both"):
        return jsonify({"error": "Invalid target"}), 400

    is_portfolio = 1 if target in ("portfolio", "both") else 0
    is_watchlist = 1 if target in ("watchlist", "both") else 0

    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE stocks SET is_portfolio = ?, is_watchlist = ? WHERE ticker = ?",
            (is_portfolio, is_watchlist, ticker),
        )
        if cur.rowcount == 0:
            return jsonify({"error": "Ticker not found"}), 404
        row = conn.execute("SELECT * FROM stocks WHERE ticker = ?", (ticker,)).fetchone()

    return jsonify(_stock_to_api(row))


@app.post("/api/stocks/<ticker>/rename")
def api_rename_stock(ticker):
    """Rename a ticker symbol, preserving alerts, watchlist memberships, and log history.
    Body: {"new": "NEWSYM"}. Useful for fixing suffixes (e.g. VMET -> VMET.TO)."""
    old = ticker.upper()
    new = (request.get_json(force=True).get("new") or "").strip().upper()
    if not new or new == old:
        return jsonify({"error": "Invalid new ticker"}), 400

    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM stocks WHERE ticker = ?", (old,)).fetchone():
            return jsonify({"error": f"{old} not found"}), 404
        if conn.execute("SELECT 1 FROM stocks WHERE ticker = ?", (new,)).fetchone():
            return jsonify({"error": f"{new} already exists"}), 409

        # Defer FK checks until commit so we can update parent + children in any order
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("UPDATE stocks            SET ticker = ? WHERE ticker = ?", (new, old))
        conn.execute("UPDATE watchlist_members SET ticker = ? WHERE ticker = ?", (new, old))
        conn.execute("UPDATE alert_rules       SET ticker = ? WHERE ticker = ?", (new, old))
        conn.execute("UPDATE alert_log         SET ticker = ? WHERE ticker = ?", (new, old))

    # Refresh price data under the new symbol (best-effort)
    try:
        fetch_and_store_all([new])
    except Exception as e:
        log.warning(f"Rename {old}->{new}: refresh failed ({e}); row kept with stale price")

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM stocks WHERE ticker = ?", (new,)).fetchone()
    return jsonify(_stock_to_api(row))


@app.post("/api/refresh")
def api_refresh_all():
    results = fetch_and_store_all()
    return jsonify({"refreshed": list(results.keys()), "count": len(results)})


@app.post("/api/refresh/<ticker>")
def api_refresh_one(ticker):
    ticker = ticker.upper()
    results = fetch_and_store_all([ticker])
    if ticker not in results:
        return jsonify({"error": "Refresh failed"}), 502
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM stocks WHERE ticker = ?", (ticker,)).fetchone()
    return jsonify(_stock_to_api(row))


@app.get("/api/validate/<ticker>")
def api_validate(ticker):
    ticker = ticker.upper()
    snapshot = validate_ticker(ticker)
    if snapshot is None:
        return jsonify({"valid": False, "ticker": ticker}), 200
    return jsonify({
        "valid": True,
        "ticker": ticker,
        "company_name": snapshot.get("company_name"),
        "current_price": snapshot.get("current_price"),
    })


# -------------------------------------------------------------------
# Alert checker (called by the cron job)
# -------------------------------------------------------------------

@app.post("/api/check-alerts")
def api_check_alerts():
    """
    Trigger a run of the alert worker inline, inside this web service.

    Protected by a shared secret: the caller must include the
    header `X-Alert-Secret` matching the ALERT_CHECK_SECRET env var.
    This prevents random internet traffic from hammering this endpoint.

    The cron job calls this URL every 5 minutes.
    Supports both a manual "?force=1" query param and a normal scheduled run.
    """
    expected = os.environ.get("ALERT_CHECK_SECRET")
    if not expected:
        log.warning("ALERT_CHECK_SECRET not set; refusing to run check-alerts")
        return jsonify({"error": "Server not configured (missing ALERT_CHECK_SECRET)"}), 500

    provided = request.headers.get("X-Alert-Secret") or request.args.get("secret")
    if provided != expected:
        return jsonify({"error": "Unauthorized"}), 401

    # Import here to avoid circular import at module load
    from worker.alerts import run_once

    force = request.args.get("force") in ("1", "true", "yes")
    try:
        run_once(force=force)
        return jsonify({"ok": True, "forced": force})
    except Exception as e:
        log.error(f"check-alerts failed: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# -------------------------------------------------------------------
# Alerts
# -------------------------------------------------------------------

@app.get("/api/alerts")
def api_list_alerts():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ar.*, s.company_name, s.last_price, s.endorsement_price
            FROM alert_rules ar
            LEFT JOIN stocks s ON s.ticker = ar.ticker
            ORDER BY ar.active DESC, ar.ticker, ar.id
        """).fetchall()
    return jsonify([_row_to_dict(r) for r in rows])


@app.post("/api/alerts")
def api_create_alert():
    data = request.get_json(force=True)
    ticker = (data.get("ticker") or "").strip().upper()
    rule_type = (data.get("rule_type") or "").strip()
    threshold = data.get("threshold")
    one_shot = 1 if data.get("one_shot", True) else 0
    note = data.get("note")

    if not ticker:
        return jsonify({"error": "Ticker required"}), 400
    if rule_type not in ALLOWED_RULE_TYPES:
        return jsonify({"error": f"rule_type must be one of {sorted(ALLOWED_RULE_TYPES)}"}), 400
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return jsonify({"error": "threshold must be a number"}), 400

    with get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM stocks WHERE ticker = ?", (ticker,)).fetchone()
        if not exists:
            return jsonify({"error": f"Ticker '{ticker}' not in your list. Add it first."}), 400

        cur = conn.execute("""
            INSERT INTO alert_rules (ticker, rule_type, threshold, active, one_shot, note)
            VALUES (?, ?, ?, 1, ?, ?)
        """, (ticker, rule_type, threshold, one_shot, note))
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM alert_rules WHERE id = ?", (new_id,)).fetchone()

    return jsonify(_row_to_dict(row)), 201


@app.patch("/api/alerts/<int:alert_id>")
def api_update_alert(alert_id):
    data = request.get_json(force=True)
    fields = {}
    if "active" in data:
        fields["active"] = 1 if data["active"] else 0
    if "threshold" in data:
        fields["threshold"] = float(data["threshold"])
    if "one_shot" in data:
        fields["one_shot"] = 1 if data["one_shot"] else 0
    if "note" in data:
        fields["note"] = data["note"]

    if not fields:
        return jsonify({"error": "No valid fields"}), 400

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [alert_id]

    with get_conn() as conn:
        cur = conn.execute(f"UPDATE alert_rules SET {set_clause} WHERE id = ?", values)
        if cur.rowcount == 0:
            return jsonify({"error": "Alert not found"}), 404
        row = conn.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_id,)).fetchone()

    return jsonify(_row_to_dict(row))


@app.delete("/api/alerts/<int:alert_id>")
def api_delete_alert(alert_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM alert_rules WHERE id = ?", (alert_id,))
        if cur.rowcount == 0:
            return jsonify({"error": "Alert not found"}), 404
    return jsonify({"ok": True})


@app.get("/api/alerts/log")
def api_alert_log():
    limit = min(int(request.args.get("limit", 50)), 500)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM alert_log ORDER BY fired_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return jsonify([_row_to_dict(r) for r in rows])


# -------------------------------------------------------------------
# Web Push notifications
# -------------------------------------------------------------------

@app.get("/api/push/public-key")
def api_push_public_key():
    """Expose the VAPID public key so the frontend can subscribe."""
    from backend.push import VAPID_PUBLIC_KEY, is_configured
    if not is_configured():
        return jsonify({"error": "Push not configured"}), 503
    return jsonify({"key": VAPID_PUBLIC_KEY})


@app.post("/api/push/subscribe")
def api_push_subscribe():
    """Register a browser push subscription. Idempotent on endpoint."""
    sub = request.get_json(force=True)
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not (endpoint and p256dh and auth):
        return jsonify({"error": "Invalid subscription payload"}), 400

    ua = request.headers.get("User-Agent", "")[:500]

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                p256dh = excluded.p256dh,
                auth   = excluded.auth,
                user_agent = excluded.user_agent
        """, (endpoint, p256dh, auth, ua))
    return jsonify({"ok": True}), 201


@app.post("/api/push/unsubscribe")
def api_push_unsubscribe():
    endpoint = (request.get_json(force=True) or {}).get("endpoint")
    if not endpoint:
        return jsonify({"error": "endpoint required"}), 400
    with get_conn() as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    return jsonify({"ok": True})


@app.post("/api/push/test")
def api_push_test():
    """Send a test push to all registered subscriptions."""
    from backend.push import send_to_all
    sent, pruned = send_to_all(
        "StockTracker test",
        "Push notifications are working.",
    )
    return jsonify({"sent": sent, "pruned": pruned})


# -------------------------------------------------------------------
# Health check (useful for Render uptime monitoring)
# -------------------------------------------------------------------

@app.get("/healthz")
def health():
    return jsonify({"ok": True})


# -------------------------------------------------------------------
# In-process alert scheduler
#
# Replaces the need for a separate Render cron job. A daemon thread starts
# when the web service boots and runs the alert worker every 5 minutes.
#
# This is used because Render's cron IP range gets blocked by Cloudflare
# when it tries to POST to the web service's public URL (error 1010). By
# running the scheduler inside the web process itself, the check logic
# runs directly against the local database — no HTTP call, no Cloudflare.
#
# Enabled by default; disable by setting ENABLE_SCHEDULER=false.
# Interval in seconds configurable via SCHEDULER_INTERVAL_SECONDS
# (default 300 = 5 minutes).
# -------------------------------------------------------------------

def _start_alert_scheduler():
    """Spawn a daemon thread that calls run_once() on an interval."""
    import threading
    import time as _time

    if os.environ.get("ENABLE_SCHEDULER", "true").lower() in ("false", "0", "no"):
        log.info("Alert scheduler disabled via ENABLE_SCHEDULER env var")
        return

    interval = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "300"))
    log.info(f"Starting in-process alert scheduler (every {interval}s)")

    def _loop():
        # Small initial delay so startup finishes cleanly before first check
        _time.sleep(30)
        from worker.alerts import run_once
        while True:
            try:
                result = run_once(force=False)
                log.info(f"Scheduler tick: {result}")
            except Exception as e:
                log.error(f"Scheduler tick failed: {e}", exc_info=True)
            _time.sleep(interval)

    t = threading.Thread(target=_loop, name="alert-scheduler", daemon=True)
    t.start()


# Start the scheduler when the module is imported (e.g., by gunicorn).
# Guarded by env var so we don't spawn a thread in test environments.
_start_alert_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
