"""
payments/models.py — Subscription & Payment Database Models

Two tables:
  - subscriptions: one per user, tracks plan status, trial, Teya token
  - payment_events: immutable log of every charge attempt
"""

from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, Text, ForeignKey, Integer
from db.database import Base


class Subscription(Base):
    """Tracks a user's subscription lifecycle.

    States (status field):
      trial     — signed up, card tokenised, no charge yet
      active    — paying, card being charged monthly
      past_due  — charge failed, retrying (up to 3 attempts)
      cancelled — user cancelled or max retries exhausted
      expired   — trial ended without conversion
    """
    __tablename__ = 'subscriptions'

    id                = Column(String(36), primary_key=True)
    user_id           = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True)

    # Plan
    status            = Column(String(20), nullable=False, default='trial')
    price_amount      = Column(Integer, nullable=False, default=2300)  # in minor units (e.g. 2300 = $23.00)
    price_currency    = Column(String(3), nullable=False, default='USD')

    # Teya/Borgun card token
    teya_token        = Column(String(255), nullable=True)   # multi-use token from Borgun RPG
    card_last_four    = Column(String(4), nullable=True)
    card_brand        = Column(String(20), nullable=True)    # Visa, Mastercard, etc.

    # Dates
    trial_start       = Column(DateTime, nullable=False, default=datetime.utcnow)
    trial_end         = Column(DateTime, nullable=True)      # trial_start + 5 days
    current_period_start = Column(DateTime, nullable=True)   # start of current billing cycle
    current_period_end   = Column(DateTime, nullable=True)   # end of current billing cycle
    cancelled_at      = Column(DateTime, nullable=True)

    # Retry state
    retry_count       = Column(Integer, nullable=False, default=0)
    next_retry_at     = Column(DateTime, nullable=True)

    created_at        = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at        = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaymentEvent(Base):
    """Immutable log of every charge attempt.

    Keeps a paper trail for debugging, receipts, and refunds.
    """
    __tablename__ = 'payment_events'

    id                = Column(String(36), primary_key=True)
    user_id           = Column(String(36), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    subscription_id   = Column(String(36), ForeignKey('subscriptions.id', ondelete='CASCADE'), nullable=False)

    # What happened
    event_type        = Column(String(30), nullable=False)   # 'charge', 'refund', 'charge_failed'
    amount            = Column(Integer, nullable=False)       # minor units
    currency          = Column(String(3), nullable=False)

    # Teya response
    teya_transaction_id = Column(String(255), nullable=True)
    teya_status       = Column(String(50), nullable=True)
    teya_response     = Column(Text, nullable=True)          # raw JSON response

    created_at        = Column(DateTime, nullable=False, default=datetime.utcnow)
