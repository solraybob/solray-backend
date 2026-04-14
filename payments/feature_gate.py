"""
payments/feature_gate.py — Premium Feature Gating

FastAPI dependency that checks subscription access before allowing
premium endpoints. Drop it into any route that should be paywalled.

Usage:
    @app.get('/some-premium-endpoint')
    async def premium(user_id: str = Depends(require_premium)):
        ...

Free features (available without subscription):
  - Registration / login
  - Basic profile and blueprint view
  - Subscription management endpoints

Premium features (require active trial or paid subscription):
  - Daily forecast
  - Higher Self chat
  - Soul connections and synergy readings
  - Transit tracking
  - Astrocartography
"""

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db, get_user_by_id
from api.auth import get_current_user_id
from payments.subscription_manager import get_subscription, has_premium_access

# ---------------------------------------------------------------------------
# Founders: permanent full access, no subscription needed.
# ---------------------------------------------------------------------------
FOUNDER_EMAILS: set[str] = {
    "kristjangilbert@gmail.com",   # Bob
    "martakarenk@gmail.com",       # Marta
    "davidsnaerj@gmail.com",       # David
    # Add Rafn's email here once confirmed
}


async def require_premium(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> str:
    """FastAPI dependency: returns user_id if they have premium access
    AND verified email, raises 403 otherwise.
    Founders bypass both checks entirely.
    """
    user = await get_user_by_id(db, user_id)

    # Founders always pass
    if user and user.email in FOUNDER_EMAILS:
        return user_id

    # Email verification check
    if user and not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address first. Check your inbox for the verification link.",
        )

    # Subscription check
    sub = await get_subscription(db, user_id)
    if not has_premium_access(sub):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Premium subscription required. Start your free trial at /subscribe.",
        )
    return user_id
