"""
payments/subscription_manager.py — Subscription Lifecycle Management

Handles the full subscription journey:
  trial (5 days) -> active (monthly) -> past_due (retries) -> cancelled/expired

All state transitions go through this module so billing rules
live in one place.
"""

import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payments.models import Subscription, PaymentEvent
from payments.teya_client import teya, TeyaError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRIAL_DAYS = 5
BILLING_CYCLE_DAYS = 30          # Roughly monthly
MAX_RETRY_ATTEMPTS = 3
RETRY_INTERVAL_HOURS = [24, 48, 72]  # Escalating retry delays
DEFAULT_PRICE = 2300             # $23.00 in minor units
DEFAULT_CURRENCY = "USD"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def get_subscription(db: AsyncSession, user_id: str) -> Optional[Subscription]:
    """Fetch the subscription row for a user, if any."""
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def _log_event(
    db: AsyncSession,
    user_id: str,
    subscription_id: str,
    event_type: str,
    amount: int,
    currency: str,
    teya_transaction_id: Optional[str] = None,
    teya_status: Optional[str] = None,
    teya_response: Optional[str] = None,
) -> PaymentEvent:
    """Write an immutable payment event row."""
    event = PaymentEvent(
        id=str(uuid.uuid4()),
        user_id=user_id,
        subscription_id=subscription_id,
        event_type=event_type,
        amount=amount,
        currency=currency,
        teya_transaction_id=teya_transaction_id,
        teya_status=teya_status,
        teya_response=teya_response,
    )
    db.add(event)
    return event


# ---------------------------------------------------------------------------
# Start trial
# ---------------------------------------------------------------------------

async def start_trial(
    db: AsyncSession,
    user_id: str,
    teya_token: Optional[str] = None,
    card_last_four: Optional[str] = None,
    card_brand: Optional[str] = None,
) -> Subscription:
    """Create a new subscription in trial state.

    Card token is optional at trial start. If the user tokenises their
    card later (before trial ends), call attach_card().
    """
    now = datetime.utcnow()
    sub = Subscription(
        id=str(uuid.uuid4()),
        user_id=user_id,
        status="trial",
        price_amount=DEFAULT_PRICE,
        price_currency=DEFAULT_CURRENCY,
        teya_token=teya_token,
        card_last_four=card_last_four,
        card_brand=card_brand,
        trial_start=now,
        trial_end=now + timedelta(days=TRIAL_DAYS),
        created_at=now,
        updated_at=now,
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    logger.info("[sub] Trial started for user %s, ends %s", user_id, sub.trial_end)
    return sub


# ---------------------------------------------------------------------------
# Attach / update card
# ---------------------------------------------------------------------------

async def attach_card(
    db: AsyncSession,
    user_id: str,
    teya_token: str,
    card_last_four: str,
    card_brand: str,
) -> Subscription:
    """Store a Teya multi-use token on an existing subscription."""
    sub = await get_subscription(db, user_id)
    if not sub:
        raise ValueError("No subscription found for this user")

    sub.teya_token = teya_token
    sub.card_last_four = card_last_four
    sub.card_brand = card_brand
    sub.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(sub)
    logger.info("[sub] Card attached for user %s (%s ...%s)", user_id, card_brand, card_last_four)
    return sub


# ---------------------------------------------------------------------------
# Convert trial to active (first charge)
# ---------------------------------------------------------------------------

async def convert_trial_to_active(db: AsyncSession, user_id: str) -> Subscription:
    """Charge the card and move from trial to active.

    Called either:
      - By the billing scheduler when trial_end is reached
      - By the user clicking "subscribe now" during trial
    """
    sub = await get_subscription(db, user_id)
    if not sub:
        raise ValueError("No subscription found")
    if not sub.teya_token:
        raise ValueError("No card on file. User must add a payment method first.")

    now = datetime.utcnow()

    try:
        result = await teya.charge_token(
            token=sub.teya_token,
            amount=sub.price_amount,
            order_id=f"solray-{sub.id}-{now.strftime('%Y%m%d')}",
        )

        # Success: move to active
        sub.status = "active"
        sub.current_period_start = now
        sub.current_period_end = now + timedelta(days=BILLING_CYCLE_DAYS)
        sub.retry_count = 0
        sub.next_retry_at = None
        sub.updated_at = now

        await _log_event(
            db,
            user_id=user_id,
            subscription_id=sub.id,
            event_type="charge",
            amount=sub.price_amount,
            currency=sub.price_currency,
            teya_transaction_id=result.get("TransactionId"),
            teya_status=result.get("ActionCode"),
            teya_response=str(result),
        )

        await db.commit()
        await db.refresh(sub)
        logger.info("[sub] Trial converted to active for user %s", user_id)
        return sub

    except TeyaError as e:
        # First charge failed: move to past_due for retries
        sub.status = "past_due"
        sub.retry_count = 1
        sub.next_retry_at = now + timedelta(hours=RETRY_INTERVAL_HOURS[0])
        sub.updated_at = now

        await _log_event(
            db,
            user_id=user_id,
            subscription_id=sub.id,
            event_type="charge_failed",
            amount=sub.price_amount,
            currency=sub.price_currency,
            teya_status=str(e.status_code),
            teya_response=e.raw_response,
        )

        await db.commit()
        await db.refresh(sub)
        logger.warning("[sub] First charge failed for user %s: %s", user_id, e.message)
        return sub


# ---------------------------------------------------------------------------
# Monthly renewal
# ---------------------------------------------------------------------------

async def renew(db: AsyncSession, sub: Subscription) -> Subscription:
    """Charge the next monthly cycle. Called by the billing scheduler."""
    if not sub.teya_token:
        logger.warning("[sub] No token for sub %s, skipping renewal", sub.id)
        return sub

    now = datetime.utcnow()

    try:
        result = await teya.charge_token(
            token=sub.teya_token,
            amount=sub.price_amount,
            order_id=f"solray-{sub.id}-{now.strftime('%Y%m%d')}",
        )

        sub.current_period_start = now
        sub.current_period_end = now + timedelta(days=BILLING_CYCLE_DAYS)
        sub.retry_count = 0
        sub.next_retry_at = None
        sub.updated_at = now

        await _log_event(
            db,
            user_id=sub.user_id,
            subscription_id=sub.id,
            event_type="charge",
            amount=sub.price_amount,
            currency=sub.price_currency,
            teya_transaction_id=result.get("TransactionId"),
            teya_status=result.get("ActionCode"),
            teya_response=str(result),
        )

        await db.commit()
        await db.refresh(sub)
        logger.info("[sub] Renewal successful for sub %s", sub.id)
        return sub

    except TeyaError as e:
        return await _handle_failed_charge(db, sub, e)


# ---------------------------------------------------------------------------
# Retry failed charges
# ---------------------------------------------------------------------------

async def retry_charge(db: AsyncSession, sub: Subscription) -> Subscription:
    """Retry a failed charge. Called by the billing scheduler for past_due subs."""
    if not sub.teya_token:
        return sub

    now = datetime.utcnow()

    try:
        result = await teya.charge_token(
            token=sub.teya_token,
            amount=sub.price_amount,
            order_id=f"solray-{sub.id}-retry-{sub.retry_count}",
        )

        # Retry succeeded: back to active
        sub.status = "active"
        sub.current_period_start = now
        sub.current_period_end = now + timedelta(days=BILLING_CYCLE_DAYS)
        sub.retry_count = 0
        sub.next_retry_at = None
        sub.updated_at = now

        await _log_event(
            db,
            user_id=sub.user_id,
            subscription_id=sub.id,
            event_type="charge",
            amount=sub.price_amount,
            currency=sub.price_currency,
            teya_transaction_id=result.get("TransactionId"),
            teya_status=result.get("ActionCode"),
            teya_response=str(result),
        )

        await db.commit()
        await db.refresh(sub)
        logger.info("[sub] Retry succeeded for sub %s", sub.id)
        return sub

    except TeyaError as e:
        return await _handle_failed_charge(db, sub, e)


async def _handle_failed_charge(
    db: AsyncSession, sub: Subscription, error: TeyaError
) -> Subscription:
    """Common logic for when a charge fails: increment retry or cancel."""
    now = datetime.utcnow()
    sub.retry_count += 1

    await _log_event(
        db,
        user_id=sub.user_id,
        subscription_id=sub.id,
        event_type="charge_failed",
        amount=sub.price_amount,
        currency=sub.price_currency,
        teya_status=str(error.status_code),
        teya_response=error.raw_response,
    )

    if sub.retry_count >= MAX_RETRY_ATTEMPTS:
        # Max retries exhausted: cancel
        sub.status = "cancelled"
        sub.cancelled_at = now
        sub.next_retry_at = None
        logger.warning("[sub] Max retries reached, cancelling sub %s", sub.id)
    else:
        sub.status = "past_due"
        idx = min(sub.retry_count, len(RETRY_INTERVAL_HOURS) - 1)
        sub.next_retry_at = now + timedelta(hours=RETRY_INTERVAL_HOURS[idx])
        logger.warning(
            "[sub] Charge failed for sub %s, retry %d scheduled at %s",
            sub.id, sub.retry_count, sub.next_retry_at,
        )

    sub.updated_at = now
    await db.commit()
    await db.refresh(sub)
    return sub


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

async def cancel_subscription(db: AsyncSession, user_id: str) -> Subscription:
    """User-initiated cancellation. Access continues until current_period_end."""
    sub = await get_subscription(db, user_id)
    if not sub:
        raise ValueError("No subscription found")

    now = datetime.utcnow()
    sub.status = "cancelled"
    sub.cancelled_at = now
    sub.next_retry_at = None
    sub.updated_at = now

    await db.commit()
    await db.refresh(sub)
    logger.info("[sub] User %s cancelled subscription", user_id)
    return sub


# ---------------------------------------------------------------------------
# Expire stale trials
# ---------------------------------------------------------------------------

async def expire_trial(db: AsyncSession, sub: Subscription) -> Subscription:
    """Mark a trial as expired (no card added before trial ended)."""
    sub.status = "expired"
    sub.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(sub)
    logger.info("[sub] Trial expired for sub %s", sub.id)
    return sub


# ---------------------------------------------------------------------------
# Access check
# ---------------------------------------------------------------------------

def has_premium_access(sub: Optional[Subscription]) -> bool:
    """Check if a user currently has access to premium features.

    Access is granted during:
      - Active trial period (even without card)
      - Active paid subscription
      - Cancelled subscription with remaining period
      - Past-due subscription (grace period while retrying)
    """
    if sub is None:
        return False

    now = datetime.utcnow()

    if sub.status == "trial":
        return sub.trial_end is not None and now < sub.trial_end

    if sub.status == "active":
        return True

    if sub.status == "past_due":
        # Grace period: keep access while retrying
        return True

    if sub.status == "cancelled":
        # Access until end of paid period
        if sub.current_period_end and now < sub.current_period_end:
            return True

    return False
