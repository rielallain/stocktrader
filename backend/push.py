"""
Web Push sending for StockTracker.

Sends notifications to all registered browser push subscriptions when
alerts fire. Uses VAPID auth (set VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, and
VAPID_CONTACT_EMAIL env vars). Dead subscriptions (410 Gone / 404) are
auto-pruned so the table stays clean.
"""
import json
import logging
import os
from typing import Optional

from pywebpush import webpush, WebPushException

from backend.db import get_conn

log = logging.getLogger(__name__)

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CONTACT_EMAIL = os.environ.get("VAPID_CONTACT_EMAIL", "mailto:admin@example.com")

if not VAPID_CONTACT_EMAIL.startswith("mailto:"):
    VAPID_CONTACT_EMAIL = f"mailto:{VAPID_CONTACT_EMAIL}"


def is_configured() -> bool:
    return bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY)


def send_to_all(title: str, body: str, url: str = "/") -> tuple[int, int]:
    """Send a push notification to every registered subscription.
    Returns (sent_ok_count, pruned_count)."""
    if not is_configured():
        log.warning("Web Push not configured; skipping send")
        return 0, 0

    payload = json.dumps({"title": title, "body": body, "url": url})

    with get_conn() as conn:
        subs = conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions"
        ).fetchall()

    if not subs:
        log.info("No push subscriptions registered")
        return 0, 0

    sent = 0
    dead_ids: list[int] = []
    for sub in subs:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CONTACT_EMAIL},
                timeout=10,
            )
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            # 404/410 = subscription gone; prune it.
            if status in (404, 410):
                dead_ids.append(sub["id"])
                log.info(f"Pruning dead push subscription id={sub['id']}")
            else:
                log.error(f"Push send failed (status={status}): {e}")
        except Exception as e:
            log.error(f"Push send unexpected error: {e}")

    if dead_ids:
        with get_conn() as conn:
            placeholders = ",".join("?" * len(dead_ids))
            conn.execute(
                f"DELETE FROM push_subscriptions WHERE id IN ({placeholders})",
                dead_ids,
            )

    log.info(f"Push: sent={sent}, pruned={len(dead_ids)}, total_subs={len(subs)}")
    return sent, len(dead_ids)
