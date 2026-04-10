"""
payments/paddle.py — Paddle payment integration for Solray

Handles:
  - Subscription verification (is this user paying?)
  - Webhook processing (new subscription, cancellation, renewal)
  - Checkout URL generation

Paddle docs: https://developer.paddle.com/api-reference
"""

import os
import json
import hmac
import hashlib
from typing import Optional
from datetime import datetime


PADDLE_API_KEY = os.environ.get('PADDLE_API_KEY', '')
PADDLE_WEBHOOK_SECRET = os.environ.get('PADDLE_WEBHOOK_SECRET', '')
PADDLE_PRICE_ID = os.environ.get('PADDLE_PRICE_ID', '')  # $23/month price ID
PADDLE_SANDBOX = os.environ.get('PADDLE_SANDBOX', 'true').lower() == 'true'

BASE_URL = 'https://sandbox-api.paddle.com' if PADDLE_SANDBOX else 'https://api.paddle.com'


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify Paddle webhook signature to prevent spoofing."""
    if not PADDLE_WEBHOOK_SECRET:
        return True  # Skip verification in dev
    expected = hmac.new(
        PADDLE_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def get_checkout_url(user_email: str, user_id: str) -> str:
    """Generate a Paddle checkout URL for the $23/month subscription."""
    if PADDLE_SANDBOX:
        base = 'https://sandbox-buy.paddle.com'
    else:
        base = 'https://buy.paddle.com'
    
    return (
        f"{base}/checkout?items[0][priceId]={PADDLE_PRICE_ID}"
        f"&items[0][quantity]=1"
        f"&customer[email]={user_email}"
        f"&customData[user_id]={user_id}"
    )


async def get_subscription_status(user_id: str) -> dict:
    """
    Check if a user has an active Paddle subscription.
    Returns: {active: bool, subscription_id: str, next_billing: str}
    """
    import httpx
    
    if not PADDLE_API_KEY:
        # Dev mode: return active for all users
        return {'active': True, 'subscription_id': None, 'next_billing': None}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{BASE_URL}/subscriptions",
                headers={'Authorization': f'Bearer {PADDLE_API_KEY}'},
                params={'custom_data[user_id]': user_id, 'status': 'active'},
            )
            data = response.json()
            subs = data.get('data', [])
            if subs:
                sub = subs[0]
                return {
                    'active': True,
                    'subscription_id': sub.get('id'),
                    'next_billing': sub.get('next_billed_at'),
                }
            return {'active': False, 'subscription_id': None, 'next_billing': None}
    except Exception:
        # Fail open in case of API issues — don't block users
        return {'active': True, 'subscription_id': None, 'next_billing': None}


def parse_webhook_event(payload: dict) -> Optional[dict]:
    """
    Parse a Paddle webhook event.
    Returns normalized event data or None if unrecognized.
    
    Relevant events:
      - subscription.created: new subscriber
      - subscription.canceled: user cancelled
      - subscription.updated: plan change or renewal
      - transaction.completed: payment processed
    """
    event_type = payload.get('event_type', '')
    data = payload.get('data', {})
    
    if event_type == 'subscription.created':
        return {
            'type': 'subscribed',
            'user_id': data.get('custom_data', {}).get('user_id'),
            'subscription_id': data.get('id'),
            'email': data.get('customer', {}).get('email'),
        }
    
    elif event_type == 'subscription.canceled':
        return {
            'type': 'cancelled',
            'user_id': data.get('custom_data', {}).get('user_id'),
            'subscription_id': data.get('id'),
            'ends_at': data.get('canceled_at'),
        }
    
    elif event_type == 'transaction.completed':
        return {
            'type': 'payment',
            'user_id': data.get('custom_data', {}).get('user_id'),
            'amount': data.get('details', {}).get('totals', {}).get('total'),
        }
    
    return None
