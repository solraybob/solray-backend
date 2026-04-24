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
# The SecurePay hosted page requires a *payment gateway id* which is a
# Teya-assigned number SEPARATE from the merchant id and SEPARATE from the
# RPG vendor id. If your merchant has only one gateway Teya sometimes lets
# you omit it entirely. Precedence: explicit env > vendor_id > omit.
TEYA_PAYMENT_GATEWAY_ID = os.environ.get("TEYA_PAYMENT_GATEWAY_ID", "")
TEYA_SECRET_KEY   = os.environ.get("TEYA_SECRET_KEY", "")
TEYA_CURRENCY     = os.environ.get("TEYA_CURRENCY", "840")  # 840 = USD, 352 = ISK

# SecurePay hosted page base URL — override to switch between test and production.
# Test env uses capital "SecurePay" in the path; live uses lowercase.
# Test:  https://test.borgun.is/SecurePay/default.aspx
# Live:  https://securepay.borgun.is/securepay/default.aspx
TEYA_SECUREPAY_URL = os.environ.get(
    "TEYA_SECUREPAY_URL",
    "https://securepay.borgun.is/securepay/default.aspx",
)

# Currencies whose minor-unit is used (cents, etc). Everything else (ISK, JPY)
# is already in major units so no /100 conversion.
_CURRENCIES_WITH_MINOR_UNITS = {"USD", "EUR", "GBP", "DKK", "NOK", "SEK", "CHF", "CAD"}

# Approximate USD->foreign-currency rates for when we charge outside USD.
# The subscription price is authored in USD cents ($23.00 = 2300) so we
# convert to the gateway currency at these rates. Override with env var
# (e.g. TEYA_USD_RATE_ISK=135) when the market moves materially.
_DEFAULT_USD_RATES = {
    "ISK": 130.0,  # $23 ≈ 2,990 ISK
    "EUR": 0.92,
    "GBP": 0.79,
    "DKK": 6.85,
    "NOK": 10.8,
    "SEK": 10.5,
    "CHF": 0.88,
    "CAD": 1.36,
    "JPY": 150.0,
}


def _usd_rate(code: str) -> float:
    override = os.environ.get(f"TEYA_USD_RATE_{code}")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    return _DEFAULT_USD_RATES.get(code, 1.0)


def _secret_key_bytes() -> bytes:
    """Return the HMAC key bytes for Teya CheckHash.

    Teya's portal delivers the SecretKey as 32 hex characters (e.g.
    "d64f2893e921a9434664668d51d00383") which represents a 16-byte binary
    key. For the HMAC signature to match, we must hex-decode before passing
    to hmac.new; using the hex STRING as the key produces a different
    signature that Teya's validator rejects.

    Detection rule: if the key is pure hex of length 32 or 64, decode it;
    otherwise assume it's already a plain string and UTF-8 encode it. This
    preserves backward compatibility for any deployments that configured
    the secret as a string.

    Explicit override via TEYA_SECRET_KEY_FORMAT = "hex" | "utf8" | "base64"
    when auto-detection is ambiguous.
    """
    key = TEYA_SECRET_KEY or ""
    override = (os.environ.get("TEYA_SECRET_KEY_FORMAT") or "").lower().strip()

    if override == "utf8":
        return key.encode("utf-8")
    if override == "hex":
        return bytes.fromhex(key)
    if override == "base64":
        return base64.b64decode(key)

    # Auto-detect: pure hex of typical HMAC key lengths gets decoded.
    if len(key) in (32, 64) and all(c in "0123456789abcdefABCDEF" for c in key):
        try:
            return bytes.fromhex(key)
        except ValueError:
            pass
    return key.encode("utf-8")


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
        success_server_url: Optional[str] = None,
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

        # paymentgatewayid is REQUIRED by Borgun's SecurePay validator in the
        # URL params. It must NOT appear in the HMAC CheckHash calculation
        # (only the 6 fields below are hashed). Precedence for the value:
        # explicit TEYA_PAYMENT_GATEWAY_ID > TEYA_VENDOR_ID. An empty
        # gateway id causes Teya to reject the request outright, so we log
        # a loud error if neither env var is set.
        gateway_id   = TEYA_PAYMENT_GATEWAY_ID or self.vendor_id or ""
        # Teya's SecurePay validator expects a numeric OrderId (per reference
        # plugins: $order->order_id). Hex/UUID strings can silently trigger
        # the generic "Unexpected error on payment page". Max length ~10-12
        # digits is safest across older test merchants.
        order_id     = str(uuid.uuid4().int)[:12]
        language     = "EN"
        error_url    = cancel_url

        # CRITICAL: Teya expects amount as a DECIMAL STRING with comma as the
        # decimal separator and exactly two fractional digits, e.g. "23,00",
        # never "2300" (minor units). Reference PHP:
        #   number_format($price, 2, ',', '')
        #
        # Our subscription price is authored in USD cents ($23.00 = 2300).
        # The gateway currency may not be USD (for an Iceland-routed merchant
        # it is typically ISK), so we must:
        #   1. Convert USD cents to USD dollars (divide by 100).
        #   2. Multiply by the USD->target rate (configurable via env, e.g.
        #      TEYA_USD_RATE_ISK=130 makes $23 land as 2,990 ISK).
        # When the gateway currency IS USD, the rate is 1.0.
        usd_dollars = amount / 100.0
        rate = 1.0 if currency_alpha == "USD" else _usd_rate(currency_alpha)
        amount_major = usd_dollars * rate
        amount_str = f"{amount_major:.2f}".replace(".", ",")

        # ReturnUrlSuccessServer is an OPTIONAL server-to-server callback
        # that Teya POSTs to after a successful payment. We don't currently
        # run a server-side callback handler, so this field stays empty.
        # Both the param AND the hash position must be empty so Teya and
        # we compute identical HMAC inputs. Prior code duplicated return_url
        # here, which worked with the reference HikaShop plugin (where the
        # same URL was shared) but breaks signature validation when the
        # param is genuinely omitted.
        server_url = (success_server_url or "").strip()

        # CheckHash = hex( HMAC-SHA256( secret_key, fields joined with "|" ) )
        # Field order per spec:
        #   MerchantId | ReturnUrlSuccess | ReturnUrlSuccessServer | OrderId | Amount | Currency
        # When ReturnUrlSuccessServer is omitted from params, its slot in
        # the hash input is an empty string, producing "...||..." between
        # positions 2 and 4. That's intentional, not a bug.
        hash_parts = [
            self.merchant_id,
            return_url,
            server_url,
            order_id,
            amount_str,
            currency_alpha,
        ]
        hash_input  = "|".join(hash_parts)
        key_bytes = _secret_key_bytes()
        hmac_digest = hmac.new(
            key_bytes,
            hash_input.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        # Borgun's SecurePay form example shows CheckHash as a 64-character
        # lowercase hex string, matching what the reference HikaShop Joomla
        # plugin sends (PHP's hash_hmac with default args). Default to hex.
        # TEYA_CHECKHASH_FORMAT=base64 is kept as an env override for any
        # merchant on an endpoint that expects the raw spec-style base64.
        hash_format = (os.environ.get("TEYA_CHECKHASH_FORMAT") or "hex").lower().strip()
        if hash_format == "base64":
            check_hash = base64.b64encode(hmac_digest).decode("utf-8")
        else:
            check_hash = hmac_digest.hex()

        # Parameter names match the reference HikaShop plugin verbatim,
        # including the mixed casing (MerchantId, Orderid, Itemdescription_1).
        # ASP.NET form binding is usually case-insensitive but we follow the
        # known-working casing to eliminate that as a variable.
        item_description = "Solray AI membership"
        # paymentgatewayid is a REQUIRED URL parameter for Teya SecurePay,
        # but it is NOT part of the HMAC input (the hash above covers only
        # MerchantId, returnurlsuccess, returnurlsuccessserver, Orderid,
        # amount, currency). Always include it in the URL. If neither env
        # var provided a value, we still include the key with an empty
        # string so the request isn't silently deformed, and log an error
        # to make the misconfiguration obvious in Railway.
        params = {
            "MerchantId":             self.merchant_id,
            "paymentgatewayid":       gateway_id,
            "currency":               currency_alpha,
            "language":               language,
            "amount":                 amount_str,
            "Orderid":                order_id,
            "reference":              order_id,
            "Itemdescription_1":      item_description,
            "Itemcount_1":            "1",
            "Itemunitamount_1":       amount_str,
            "Itemamount_1":           amount_str,
            "returnurlsuccess":       return_url,
            "returnurlcancel":        cancel_url,
            "returnurlerror":         error_url,
            "checkhash":              check_hash,
        }
        # Only include server callback URL when one was actually provided.
        # Omitting it keeps the URL shorter AND ensures the hash position
        # 3 (empty string) matches what Teya will recompute.
        if server_url:
            params["returnurlsuccessserver"] = server_url

        session_url = TEYA_SECUREPAY_URL + "?" + urlencode(params)

        # Force-log at WARNING so Railway surfaces this regardless of how the
        # root logger ends up configured. Redact nothing except the raw secret
        # key. The hash itself is derived so it's safe to log for debugging.
        logger.warning(
            "[Teya] SecurePay session\n"
            "  gateway_url:    %s\n"
            "  merchant_id:    %r\n"
            "  gateway_id:     %r\n"
            "  currency:       %s (raw=%s)\n"
            "  usd_rate:       %.4f\n"
            "  amount:         %s\n"
            "  order_id:       %s\n"
            "  return_url:     %s\n"
            "  secret_key_len: %d chars / %d bytes (%s)\n"
            "  hash_input:     %s\n"
            "  check_hash:     %s (format=%s)\n"
            "  session_url:    %s",
            TEYA_SECUREPAY_URL,
            self.merchant_id,
            gateway_id,
            currency_alpha, raw_currency,
            rate,
            amount_str,
            order_id,
            return_url,
            len(TEYA_SECRET_KEY or ""),
            len(key_bytes),
            "hex-decoded" if len(key_bytes) != len(TEYA_SECRET_KEY or "") else "utf-8",
            hash_input,
            check_hash,
            hash_format,
            session_url,
        )
        if not TEYA_SECRET_KEY:
            logger.error("[Teya] TEYA_SECRET_KEY is empty, CheckHash will fail verification")
        if not self.merchant_id:
            logger.error("[Teya] TEYA_MERCHANT_ID is empty, SecurePay will reject the request")
        if not gateway_id:
            logger.error(
                "[Teya] paymentgatewayid is empty. Set TEYA_PAYMENT_GATEWAY_ID "
                "or TEYA_VENDOR_ID in Railway. SecurePay requires this value "
                "in the URL params even though it is excluded from the HMAC."
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
