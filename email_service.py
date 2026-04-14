"""
email_service.py — Email Sending via Resend

Handles transactional emails: verification, welcome, payment receipts.
Uses Resend (resend.com) for delivery.

Required env var:
  RESEND_API_KEY — your Resend API key

Optional:
  EMAIL_FROM — sender address (default: noreply@solray.ai)
"""

import os
import logging
import secrets
import httpx

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Solray <noreply@solray.ai>")
APP_URL = os.environ.get("APP_URL", "https://app.solray.ai")


def generate_verification_token() -> str:
    """Create a cryptographically secure 32-byte hex token."""
    return secrets.token_hex(32)


async def send_verification_email(to_email: str, name: str, token: str) -> bool:
    """Send a verification email with a one-click confirm link.

    Returns True if the email was accepted by Resend, False otherwise.
    """
    verify_url = f"{APP_URL}/verify-email?token={token}"

    html = f"""
    <div style="font-family: 'Inter', system-ui, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 24px; color: #e8e0cc; background: #050f08;">
      <div style="text-align: center; margin-bottom: 32px;">
        <h1 style="font-family: 'Cormorant Garamond', Georgia, serif; font-size: 28px; font-weight: 300; color: #e8e0cc; margin: 0;">
          Welcome to Solray
        </h1>
      </div>

      <p style="font-size: 15px; line-height: 1.6; color: #8a9e8d; margin-bottom: 24px;">
        {name}, one step to go. Tap the button below to verify your email and unlock your full reading.
      </p>

      <div style="text-align: center; margin: 32px 0;">
        <a href="{verify_url}"
           style="display: inline-block; padding: 14px 36px; background: #e8821a; color: #050f08;
                  font-size: 14px; font-weight: 500; letter-spacing: 0.5px; text-decoration: none;
                  border-radius: 2px;">
          Verify Email
        </a>
      </div>

      <p style="font-size: 12px; color: #8a9e8d; line-height: 1.5;">
        If the button does not work, copy this link into your browser:
      </p>
      <p style="font-size: 12px; color: #8a9e8d; word-break: break-all;">
        {verify_url}
      </p>

      <hr style="border: none; border-top: 1px solid #1a3020; margin: 32px 0;" />

      <p style="font-size: 11px; color: #8a9e8d; text-align: center;">
        Solray. Living by design.
      </p>
    </div>
    """

    return await _send(
        to=to_email,
        subject="Verify your email",
        html=html,
    )


async def _send(to: str, subject: str, html: str) -> bool:
    """Low-level Resend API call."""
    if not RESEND_API_KEY:
        logger.warning("[email] RESEND_API_KEY not set, skipping email to %s", to)
        return False

    payload = {
        "from": EMAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": html,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
            )

        if resp.status_code in (200, 201):
            logger.info("[email] Verification email sent to %s", to)
            return True
        else:
            logger.error(
                "[email] Resend error %d: %s", resp.status_code, resp.text[:300]
            )
            return False

    except Exception as e:
        logger.error("[email] Failed to send to %s: %s", to, e)
        return False
