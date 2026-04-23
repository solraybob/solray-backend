"""
payments/billing_scheduler.py — Background Billing Scheduler

Runs periodically (via FastAPI lifespan or external cron) to:
  1. Expire trials that ended without a card on file
  2. Convert trials with a card into active subscriptions (first charge)
  3. Renew active subscriptions whose billing period has ended
  4. Retry failed charges for past_due subscriptions

Designed to be idempotent: safe to run multiple times without
double-charging (each operation checks current state before acting).
"""

import logging
import asyncio
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from payments.models import Subscription
from payments.subscription_manager import (
    expire_trial,
    convert_trial_to_active,
    renew,
    retry_charge,
    start_trial,
    get_subscription,
)

logger = logging.getLogger(__name__)


async def run_billing_cycle():
    """Execute one full billing cycle. Call this from a scheduler or cron endpoint."""
    logger.info("[billing] Starting billing cycle at %s", datetime.utcnow().isoformat())

    async with AsyncSessionLocal() as db:
        await _expire_stale_trials(db)
        await _convert_ready_trials(db)
        await _renew_active_subscriptions(db)
        await _retry_failed_charges(db)

    logger.info("[billing] Billing cycle complete")


# ---------------------------------------------------------------------------
# Step 1: Expire trials that ended without a card
# ---------------------------------------------------------------------------

async def _expire_stale_trials(db: AsyncSession):
    """Trials past their end date with no card on file become expired."""
    now = datetime.utcnow()
    result = await db.execute(
        select(Subscription).where(
            Subscription.status == "trial",
            Subscription.trial_end < now,
            Subscription.teya_token.is_(None),
        )
    )
    stale = result.scalars().all()

    for sub in stale:
        try:
            await expire_trial(db, sub)
        except Exception as e:
            logger.error("[billing] Failed to expire trial %s: %s", sub.id, e)


# ---------------------------------------------------------------------------
# Step 2: Convert trials that are ready (card on file, trial ended)
# ---------------------------------------------------------------------------

async def _convert_ready_trials(db: AsyncSession):
    """Trials past their end date with a card get charged and converted."""
    now = datetime.utcnow()
    result = await db.execute(
        select(Subscription).where(
            Subscription.status == "trial",
            Subscription.trial_end < now,
            Subscription.teya_token.isnot(None),
        )
    )
    ready = result.scalars().all()

    for sub in ready:
        try:
            await convert_trial_to_active(db, sub.user_id)
        except Exception as e:
            logger.error("[billing] Failed to convert trial %s: %s", sub.id, e)


# ---------------------------------------------------------------------------
# Step 3: Renew active subscriptions whose period has ended
# ---------------------------------------------------------------------------

async def _renew_active_subscriptions(db: AsyncSession):
    """Active subs past their current_period_end get charged for the next cycle."""
    now = datetime.utcnow()
    result = await db.execute(
        select(Subscription).where(
            Subscription.status == "active",
            Subscription.current_period_end < now,
        )
    )
    due = result.scalars().all()

    for sub in due:
        try:
            await renew(db, sub)
        except Exception as e:
            logger.error("[billing] Failed to renew sub %s: %s", sub.id, e)


# ---------------------------------------------------------------------------
# Step 4: Retry failed charges
# ---------------------------------------------------------------------------

async def _retry_failed_charges(db: AsyncSession):
    """Past-due subs whose next_retry_at has passed get another attempt."""
    now = datetime.utcnow()
    result = await db.execute(
        select(Subscription).where(
            Subscription.status == "past_due",
            Subscription.next_retry_at < now,
        )
    )
    retries = result.scalars().all()

    for sub in retries:
        try:
            await retry_charge(db, sub)
        except Exception as e:
            logger.error("[billing] Failed to retry charge for sub %s: %s", sub.id, e)


# ---------------------------------------------------------------------------
# One-time backfill: give every existing user a 5-day trial from today
# ---------------------------------------------------------------------------

async def backfill_trials_for_existing_users() -> int:
    """
    For every user who has no subscription row yet, start a fresh 5-day
    trial from right now. Idempotent: users with an existing subscription
    (trial, active, past_due, cancelled, expired) are skipped.

    Safe to call on every boot. Once all users have a subscription, this
    becomes a cheap no-op loop over the user table.
    """
    from db.database import User

    started = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        users = result.scalars().all()

        for user in users:
            existing = await get_subscription(db, user.id)
            if existing is not None:
                continue
            try:
                await start_trial(db, user.id)
                started += 1
            except Exception as e:
                logger.warning(
                    "[backfill] Could not start trial for user %s: %s",
                    user.id, e,
                )

    if started:
        logger.info("[backfill] Started a 5-day trial for %d existing users", started)
    return started


# ---------------------------------------------------------------------------
# Background task loop (for FastAPI lifespan)
# ---------------------------------------------------------------------------

async def billing_loop(interval_minutes: int = 60):
    """Run the billing cycle on an interval. Start this as a background task.

    Usage in FastAPI lifespan:
        @asynccontextmanager
        async def lifespan(app):
            task = asyncio.create_task(billing_loop(interval_minutes=60))
            yield
            task.cancel()
    """
    while True:
        try:
            await run_billing_cycle()
        except Exception as e:
            logger.error("[billing] Billing loop error: %s", e)

        await asyncio.sleep(interval_minutes * 60)
