"""
Market hours detection.

Returns the US equity market status ('open', 'closed', 'pre', 'post')
based on NYSE/NASDAQ regular hours (9:30 AM - 4:00 PM ET, weekdays).

Uses zoneinfo (stdlib in Python 3.9+). No external dependencies.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# US market holidays 2026 (NYSE)
HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
}


def market_status() -> dict:
    """
    Returns {'status': 'open'|'closed'|'pre'|'post', 'et_time': '...'}
    """
    now_et = datetime.now(ET)
    now_time = now_et.time()
    weekday = now_et.weekday()  # 0=Mon, 6=Sun
    date_str = now_et.strftime("%Y-%m-%d")

    is_weekend = weekday >= 5
    is_holiday = date_str in HOLIDAYS_2026

    if is_weekend or is_holiday:
        return {"status": "closed", "et_time": now_et.isoformat()}

    pre_open = time(4, 0)
    regular_open = time(9, 30)
    regular_close = time(16, 0)
    after_close = time(20, 0)

    if regular_open <= now_time < regular_close:
        status = "open"
    elif pre_open <= now_time < regular_open:
        status = "pre"
    elif regular_close <= now_time < after_close:
        status = "post"
    else:
        status = "closed"

    return {"status": status, "et_time": now_et.isoformat()}
