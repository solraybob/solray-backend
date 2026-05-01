"""
analytics/canaries.py — automated detection of payment + access regressions.

The single most expensive class of bug for Solray is the silent payment
break: a hash error, a misconfigured env var, an upstream API change
that causes every subscription attempt to fail without any of the
existing log lines surfacing as an alert. On 2026-04-26 we lost six
hours of paying-customer-time to exactly that pattern. This module
exists so it never happens again.

How it works:

    Every 15 minutes, a Railway cron task POSTs to
    /admin/canaries/run-internal (or imports run_canary_checks() as a
    standalone script). The function queries payment_events and
    analytics_events for known failure patterns. If any threshold is
    crossed, a single Slack/Telegram/email message is sent to the
    webhook URL configured in CANARY_ALERT_WEBHOOK.

    The checks are deliberately blunt — we want false positives over
    false negatives. A late-night noise alert is annoying; a missed
    payment outage costs subscribers.

Configuration:

    CANARY_ALERT_WEBHOOK   — Slack incoming-webhook URL, Telegram bot
                              endpoint, or any HTTPS POST receiver that
                              accepts {"text": "..."} JSON.
    CANARY_DISABLE         — set to "1" to silence canaries entirely
                              (use during planned outages or migrations).
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("solray.canaries")


# ---------------------------------------------------------------------------
# Thresholds — tuned for "wake the founder" sensitivity
# ---------------------------------------------------------------------------

# Window over which we evaluate every check.
LOOKBACK_HOURS = 1

# Subscription-success ratio: charge events / session_created events.
# Below this and we suspect Teya is failing systemically.
SUB_SUCCESS_RATIO_FLOOR = 0.5

# Below this many session_created events, the success ratio is too noisy
# to base alerts on (one failed test transaction in a quiet hour shouldn't
# scream).
SUB_MIN_SESSIONS_FOR_RATIO = 3

# Absolute count of failed Teya callbacks (event_type='charge_failed' or
# 'session_failed' in payment_events). One failure may be a card decline;
# multiple in a window means something systemic.
FAILED_PAYMENT_COUNT_THRESHOLD = 2

# Registration drop-off: how many register_success events expected per
# rolling-24h baseline. If current hour count is below 30% of the baseline
# rate AND the absolute number of attempts is non-trivial, alert.
REGISTRATION_DROP_RATIO = 0.3
REGISTRATION_MIN_ATTEMPTS_FOR_ALERT = 5  # don't yell during quiet hours


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------

@dataclass
class CanaryReport:
    timestamp: str
    alerts: list[str] = field(default_factory=list)
    metrics: dict[str, float | int] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return not self.alerts


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

async def _count(db: AsyncSession, sql: str, params: dict) -> int:
    """Defensive count helper — returns 0 on DB error so a single broken
    check can't take down the whole canary run.
    """
    try:
        row = (await db.execute(text(sql), params)).first()
        return int(row[0] if row and row[0] is not None else 0)
    except Exception as e:
        logger.warning("[canary] count query failed (%s): %s", sql, e)
        return 0


async def check_subscription_success_rate(db: AsyncSession, since: datetime) -> tuple[Optional[str], dict]:
    sessions = await _count(
        db,
        "SELECT COUNT(*) FROM payment_events WHERE event_type = 'session_created' AND created_at >= :since",
        {"since": since},
    )
    charges = await _count(
        db,
        "SELECT COUNT(*) FROM payment_events WHERE event_type = 'charge' AND created_at >= :since",
        {"since": since},
    )
    metrics = {"sessions_created": sessions, "charges": charges}
    if sessions < SUB_MIN_SESSIONS_FOR_RATIO:
        return None, metrics                       # too few to draw conclusions
    ratio = charges / sessions
    metrics["success_ratio"] = round(ratio, 3)
    if ratio < SUB_SUCCESS_RATIO_FLOOR:
        return (
            f"⚠ Subscription success ratio {ratio:.0%} over the last "
            f"{LOOKBACK_HOURS}h ({charges}/{sessions}). Threshold "
            f"{SUB_SUCCESS_RATIO_FLOOR:.0%}. Check Teya hash + Railway env."
        ), metrics
    return None, metrics


async def check_failed_payment_count(db: AsyncSession, since: datetime) -> tuple[Optional[str], dict]:
    failed = await _count(
        db,
        """
        SELECT COUNT(*) FROM payment_events
        WHERE event_type IN ('charge_failed', 'session_failed')
          AND created_at >= :since
        """,
        {"since": since},
    )
    metrics = {"failed_payment_events": failed}
    if failed >= FAILED_PAYMENT_COUNT_THRESHOLD:
        return (
            f"⚠ {failed} failed payment events in the last {LOOKBACK_HOURS}h. "
            f"Threshold {FAILED_PAYMENT_COUNT_THRESHOLD}. Inspect Railway "
            f"logs for [Teya] errors."
        ), metrics
    return None, metrics


async def check_registration_drop_off(db: AsyncSession, now: datetime, since: datetime) -> tuple[Optional[str], dict]:
    """Compares register_success events in the lookback window to the
    rolling 24-hour baseline. Catches "registration is silently broken"
    scenarios where /users/register starts 500ing.
    """
    recent = await _count(
        db,
        """
        SELECT COUNT(*) FROM analytics_events
        WHERE event_name = 'register_success' AND created_at >= :since
        """,
        {"since": since},
    )
    baseline_24h = await _count(
        db,
        """
        SELECT COUNT(*) FROM analytics_events
        WHERE event_name = 'register_success'
          AND created_at >= :baseline_since
          AND created_at < :since
        """,
        {"since": since, "baseline_since": now - timedelta(hours=24)},
    )
    metrics = {"registrations_recent": recent, "registrations_baseline_24h": baseline_24h}
    if baseline_24h < REGISTRATION_MIN_ATTEMPTS_FOR_ALERT:
        return None, metrics                       # too quiet to alert
    expected = baseline_24h * (LOOKBACK_HOURS / 24.0)
    if expected <= 0 or recent / expected >= REGISTRATION_DROP_RATIO:
        return None, metrics
    return (
        f"⚠ Only {recent} new registrations in the last {LOOKBACK_HOURS}h "
        f"vs. expected ≈{expected:.1f} from 24h baseline. Possible "
        f"signup outage. Check /users/register logs."
    ), metrics


async def check_stuck_on_subscribe(db: AsyncSession, since: datetime) -> tuple[Optional[str], dict]:
    """Counts users whose last analytics event was subscribe_view but who
    never went on to tap the card button or activate. >3 in a window
    suggests the subscribe page itself is misbehaving.
    """
    stuck = await _count(
        db,
        """
        SELECT COUNT(DISTINCT user_id) FROM analytics_events ae
        WHERE ae.event_name = 'subscribe_view'
          AND ae.created_at >= :since
          AND NOT EXISTS (
              SELECT 1 FROM analytics_events ae2
              WHERE ae2.user_id = ae.user_id
                AND ae2.event_name IN ('subscribe_card_tap', 'subscribe_activated')
                AND ae2.created_at >= ae.created_at
          )
        """,
        {"since": since},
    )
    metrics = {"users_stuck_on_subscribe": stuck}
    if stuck >= 3:
        return (
            f"⚠ {stuck} users hit /subscribe in the last {LOOKBACK_HOURS}h "
            f"without tapping a payment action. Subscribe page may have "
            f"a UI regression."
        ), metrics
    return None, metrics


# ---------------------------------------------------------------------------
# Orchestration + delivery
# ---------------------------------------------------------------------------

async def run_canary_checks(db: AsyncSession, *, send_alert: bool = True) -> CanaryReport:
    """Run every check, optionally fire the webhook on findings, return
    the aggregated report (so the admin endpoint can render it).
    """
    if os.environ.get("CANARY_DISABLE") == "1":
        return CanaryReport(timestamp=datetime.utcnow().isoformat(),
                            alerts=[],
                            metrics={"disabled": 1})

    now = datetime.utcnow()
    since = now - timedelta(hours=LOOKBACK_HOURS)
    report = CanaryReport(timestamp=now.isoformat())

    for check_fn in (
        check_subscription_success_rate(db, since),
        check_failed_payment_count(db, since),
        check_registration_drop_off(db, now, since),
        check_stuck_on_subscribe(db, since),
    ):
        try:
            alert, metrics = await check_fn
            report.metrics.update(metrics)
            if alert:
                report.alerts.append(alert)
        except Exception as e:
            logger.exception("[canary] check failed: %s", e)
            report.alerts.append(f"⚠ Canary check itself errored: {e!r}")

    if report.alerts and send_alert:
        await _send_webhook(report)

    return report


async def _send_webhook(report: CanaryReport) -> None:
    url = os.environ.get("CANARY_ALERT_WEBHOOK")
    if not url:
        logger.warning("[canary] alert(s) firing but CANARY_ALERT_WEBHOOK not set; skipping send")
        return
    try:
        import httpx
    except ImportError:
        logger.error("[canary] httpx not installed; cannot send webhook")
        return

    body = {
        "text": (
            f"*Solray canary*  ({report.timestamp})\n"
            + "\n".join(report.alerts)
            + f"\n\nMetrics: `{report.metrics}`"
        )
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
    except Exception as e:
        logger.error("[canary] webhook delivery failed: %s", e)


# ---------------------------------------------------------------------------
# Stand-alone runner — Railway cron entrypoint
# ---------------------------------------------------------------------------
#
# Railway cron config (railway.json -> crons):
#   {
#     "command": "python -m analytics.canaries",
#     "schedule": "*/15 * * * *"
#   }
#
# The script opens its own DB session, runs the checks, exits. Stdout
# carries a JSON-ish summary so Railway logs read at a glance.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import json
    from db.database import get_db_url, AsyncSession
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    async def _main():
        engine = create_async_engine(get_db_url())
        Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with Session() as db:
            report = await run_canary_checks(db, send_alert=True)
            print(json.dumps({
                "ts":      report.timestamp,
                "healthy": report.healthy,
                "alerts":  report.alerts,
                "metrics": report.metrics,
            }, default=str))

    asyncio.run(_main())
