"""
payments/teya_client.py — Async HTTP Client for Borgun RPG (Teya) API

Wraps the Borgun Restful Payment Gateway for:
  - Creating multi-use card tokens (for recurring billing)
  - Charging a saved token
  - Refunding a transaction

Docs: https://docs.borgun.is / https://docs.borgun.com

Auth: HTTP Basic with MerchantId (private key) provided via env vars.
"""

import os
import json
import logging
import hashlib
import hmac
import base64
import uuid
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

TEYA_BASE_URL     = os.environ.get("TEYA_BASE_URL", "https://securepay.borgun.is/rpg")
TEYA_MERCHANT_ID  = os.environ.get("TEYA_MERCHANT_ID", "")
TEYA_PUBLIC_KEY   = os.environ.get("TEYA_PUBLIC_KEY", "")
TEYA_PRIVATE_KEY  = os.environ.get("TEYA_PRIVATE_KEY", "")
TEYA_VENDOR_ID    = os.environ.get("TEYA_VENDOR_ID", "")
TEYA_SECRET_KEY   = os.environ.get("TEYA_SECRET_KEY", "")
TEYA_CURRENCY     = os.environ.get("TEYA_CURRENCY", "840")  # 840 = USD, 352 = ISK

# SecurePay hosted page base URL — override to switch between test and production.
# Test:  https://test.borgun.is/securepay/default.aspx
# Live:  https://securepay.borgun.is/securepay/default.aspx
TEYA_SECUREPAY_URL = os.environ.get(
    "TEYA_SECUREPAY_URL",
    "https://securepay.borgun.is/securepay/default.aspx",
)


class TeyaError(Exception):
    """Raised when the Borgun RPG API returns an error."""

    def __init__(self, message: str, status_code: int = 0, raw_response: str = ""):
        self.message = message
        self.status_code = status_code
        self.raw_response = raw_response
        super().__init__(message)


class TeyaClient:
    """Async wrapper around the Borgun RPG REST API."""

    def __init__(self):
        self.base_url = TEYA_BASE_URL.rstrip("/")
        self.merchant_id = TEYA_MERCHANT_ID
        self.public_key = TEYA_PUBLIC_KEY
        self.private_key = TEYA_PRIVATE_KEY
        self.vendor_id = TEYA_VENDOR_ID

    def _auth(self) -> tuple[str, str]:
        """HTTP Basic auth credentials for Borgun RPG.
        Uses public key as username if available, falls back to merchant_id.
        """
        username = self.public_key or self.merchant_id
        return (username, self.private_key)

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Token creation
    # ------------------------------------------------------------------

    async def create_multi_use_token(
        self,
        pan: str,
        exp_month: str,
        exp_year: str,
        cvc: str,
    ) -> dict:
        """Tokenise a card for recurring charges.

        Returns dict with:
          - Token: the multi-use token string (e.g. "tm_...")
          - CardType: Visa, Mastercard, etc.
          - PAN: masked PAN (last four visible)

        Note: In production, card details should come via Borgun SecurePay
        hosted form so raw PAN never touches our server. This method exists
        for the RPG direct integration path.
        """
        payload = {
            "PAN": pan,
            "ExpMonth": exp_month,
            "ExpYear": exp_year,
            "CVC": cvc,
            "Currency": TEYA_CURRENCY,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/token/multi",
                json=payload,
                auth=self._auth(),
                headers=self._headers(),
            )

        return self._handle_response(resp, "create_multi_use_token")

    # ------------------------------------------------------------------
    # Charging
    # ------------------------------------------------------------------

    async def charge_token(
        self,
        token: str,
        amount: int,
        currency: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> dict:
        """Charge a multi-use token.

        Args:
            token: Multi-use token from create_multi_use_token.
            amount: Amount in minor units (e.g. 2300 = $23.00).
            currency: ISO 4217 numeric code. Defaults to TEYA_CURRENCY.
            order_id: Optional merchant reference for this charge.

        Returns dict with:
          - TransactionId: Borgun transaction reference
          - ActionCode: "000" = approved
          - Message: human-readable status
        """
        payload = {
            "TransactionType": "Sale",
            "Amount": amount,
            "Currency": currency or TEYA_CURRENCY,
            "PaymentMethod": {
                "PaymentType": "TokenMulti",
                "Token": token,
            },
        }
        if order_id:
            payload["OrderId"] = order_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/payment",
                json=payload,
                auth=self._auth(),
                headers=self._headers(),
            )

        return self._handle_response(resp, "charge_token")

    # ------------------------------------------------------------------
    # Refunds
    # ------------------------------------------------------------------

    async def refund(
        self,
        transaction_id: str,
        amount: int,
        currency: Optional[str] = None,
    ) -> dict:
        """Refund a previous charge (full or partial).

        Args:
            transaction_id: The TransactionId from the original charge.
            amount: Amount to refund in minor units.
            currency: ISO 4217 numeric code.

        Returns dict with TransactionId and ActionCode.
        """
        payload = {
            "TransactionType": "Refund",
            "Amount": amount,
            "Currency": currency or TEYA_CURRENCY,
            "OriginalTransactionId": transaction_id,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/payment",
                json=payload,
                auth=self._auth(),
                headers=self._headers(),
            )

        return self._handle_response(resp, "refund")

    # ------------------------------------------------------------------
    # SecurePay hosted flow (card tokenisation without PAN on our server)
    # ------------------------------------------------------------------

    async def create_securepay_session(
        self,
        return_url: str,
        cancel_url: str,
        amount: int = 0,
        currency: Optional[str] = None,
    ) -> dict:
        """Generate a Borgun SecurePay hosted page URL for card entry.

        Borgun SecurePay is a separate hosted-page product from the RPG API.
        No API call is needed: we build the redirect URL locally with a
        CheckHash signature so Borgun can verify the request is authentic.

        The user is redirected to Borgun's page, enters card details there,
        and Borgun redirects back to return_url with a multi-use token.

        Returns dict with:
          - SessionUrl: the URL to redirect the user to
          - SessionToken: the order reference for this session
        """
        # Borgun SecurePay uses alphabetic currency codes, not ISO 4217 numeric.
        # Map numeric codes to alphabetic if needed.
        numeric_to_alpha = {"840": "USD", "978": "EUR", "352": "ISK", "826": "GBP"}
        raw_currency = currency or TEYA_CURRENCY
        currency_alpha = numeric_to_alpha.get(raw_currency, raw_currency)

        gateway_id   = self.vendor_id or "1"
        order_id     = uuid.uuid4().hex[:20]
        language     = "EN"
        amount_str   = str(amount)
        server_url   = ""   # optional server-side callback; leave blank
        error_url    = cancel_url

        # CheckHash = Base64( HMAC-SHA256( secret_key, fields joined with "|" ) )
        # Borgun SecurePay field order per spec:
        #   MerchantId | ReturnUrlSuccess | ReturnUrlSuccessServer | OrderId | Amount | Currency
        hash_parts = [
            self.merchant_id,
            return_url,
            server_url,
            order_id,
            amount_str,
            currency_alpha,
        ]
        hash_input  = "|".join(hash_parts)
        check_hash  = base64.b64encode(
            hmac.new(
                TEYA_SECRET_KEY.encode("utf-8"),
                hash_input.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        params = {
            "merchantid":       self.merchant_id,
            "paymentgatewayid": gateway_id,
            "currency":         currency_alpha,
            "language":         language,
            "amount":           amount_str,
            "orderid":          order_id,
            "returnurlsuccess": return_url,
            "returnurlcancel":  cancel_url,
            "returnurlerror":   error_url,
            "checkhash":        check_hash,
        }
        # Only include server callback URL if explicitly set — empty string causes Borgun to error
        if server_url:
            params["returnurlsuccessserver"] = server_url

        session_url = TEYA_SECUREPAY_URL + "?" + urlencode(params)

        logger.info(
            "[Teya] SecurePay URL generated: orderid=%s merchant=%s currency=%s amount=%s",
            order_id, self.merchant_id, currency_alpha, amount_str,
        )

        return {"SessionUrl": session_url, "SessionToken": order_id}

    # ------------------------------------------------------------------
    # Response handling
    # ------------------------------------------------------------------

    def _handle_response(self, resp: httpx.Response, operation: str) -> dict:
        """Parse response, raise TeyaError on failure."""
        raw = resp.text

        if resp.status_code >= 400:
            logger.error(
                "[Teya] %s failed: HTTP %d — %s",
                operation, resp.status_code, raw[:500],
            )
            raise TeyaError(
                message=f"Teya {operation} failed (HTTP {resp.status_code})",
                status_code=resp.status_code,
                raw_response=raw,
            )

        try:
            data = resp.json()
        except Exception:
            raise TeyaError(
                message=f"Teya {operation}: invalid JSON response",
                status_code=resp.status_code,
                raw_response=raw,
            )

        # Borgun uses ActionCode "000" for success on payment endpoints
        action_code = data.get("ActionCode", "")
        if action_code and action_code != "000":
            msg = data.get("Message", "Unknown error")
            logger.warning(
                "[Teya] %s declined: ActionCode=%s Message=%s",
                operation, action_code, msg,
            )
            raise TeyaError(
                message=f"Teya {operation} declined: {msg} (code {action_code})",
                status_code=resp.status_code,
                raw_response=raw,
            )

        return data


# Module-level singleton
teya = TeyaClient()
