import base64
import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.models import Hold
from app.pricing import PACKAGES

SQUARE_VERSION = "2026-09-16"
BASE_URLS = {"sandbox": "https://connect.squareupsandbox.com", "production": "https://connect.squareup.com"}
SIGNATURE_HEADER = "x-square-hmacsha256-signature"
REQUEST_TIMEOUT_SECONDS = 10.0
# Square's own limits on the refund request.
SQUARE_IDEMPOTENCY_KEY_MAX = 45
SQUARE_REFUND_REASON_MAX = 192

log = logging.getLogger(__name__)


class SquareError(Exception):
    pass


@dataclass(frozen=True)
class PaymentLink:
    id: str
    url: str
    order_id: str


@dataclass(frozen=True)
class RefundResult:
    id: str
    status: str


@dataclass(frozen=True)
class PaymentResult:
    """What Square says it did with the card. The token that produced it is never kept."""

    id: str
    status: str
    amount_cents: int
    reference_id: str | None
    order_id: str | None
    card_fingerprint: str | None


class CardDeclined(SquareError):
    """Square refused the card itself. The hold and the date survive; another card may work."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# Square's own words are written for a developer. These are written for a parent at a checkout.
DECLINE_MESSAGES = {
    "CARD_DECLINED": "Your bank declined that card. Try another card, or call them and try again.",
    "GENERIC_DECLINE": "Your bank declined that card. Try another card, or call them and try again.",
    "INSUFFICIENT_FUNDS": "That card was declined for insufficient funds. Try another card.",
    "CVV_FAILURE": "That security code didn't match. Check the three digits on the back and try again.",
    "ADDRESS_VERIFICATION_FAILURE": "That postal code didn't match your card. Check it and try again.",
    "INVALID_EXPIRATION": "That expiry date looks wrong. Check it and try again.",
    "CARD_EXPIRED": "That card has expired. Try another card.",
    "CARD_NOT_SUPPORTED": "That card type isn't supported. Try another card.",
    "PAYMENT_LIMIT_EXCEEDED": "Your bank declined that card for exceeding a limit. Try another card.",
}
DECLINE_FALLBACK = "That card was declined. Try another card, or call (646) 203-8087 and we'll help."


def payment_idempotency_key(card_token: str) -> str:
    """Derived from the token, not from the hold, and deliberately so.

    A double-submit of one card replays the same token, so Square returns the first payment instead of
    charging twice. A retry after a decline tokenizes afresh and gets a different key, so it is allowed
    through. A key fixed per hold would do the first job and fail the second: Square would replay the
    decline and the parent could never pay for their own party.
    """
    return hashlib.sha256(card_token.encode()).hexdigest()[:SQUARE_IDEMPOTENCY_KEY_MAX]


def create_payment(settings: Settings, hold: Hold, card_token: str) -> PaymentResult:
    """Charge the deposit. The amount is the hold's own deposit_cents and has never been client-supplied."""
    if settings.fake_square_enabled:
        return PaymentResult(
            id=f"dev-pay-{uuid.uuid4()}",
            status="COMPLETED",
            amount_cents=hold.deposit_cents,
            reference_id=str(hold.id),
            order_id=None,
            card_fingerprint="dev-fingerprint",
        )
    if not settings.square_access_token or not settings.square_location_id:
        raise SquareError("Square is not configured")
    body = {
        "idempotency_key": payment_idempotency_key(card_token),
        "source_id": card_token,
        "amount_money": {"amount": hold.deposit_cents, "currency": "USD"},
        "location_id": settings.square_location_id,
        # 40 characters at Square; a UUID is 36. This is how the webhook backstop finds the hold when
        # there is no order, so it is not decoration.
        "reference_id": str(hold.id),
        "note": f"Encore party deposit {hold.id}",
        "buyer_email_address": hold.email,
    }
    headers = {
        "Authorization": f"Bearer {settings.square_access_token}",
        "Square-Version": SQUARE_VERSION,
        "Content-Type": "application/json",
    }
    try:
        with http_client(settings) as client:
            response = client.post("/v2/payments", json=body, headers=headers)
    except httpx.HTTPError as error:
        log.warning("Square payment request failed: %s", type(error).__name__)
        raise SquareError("Square unreachable") from error
    if response.status_code != 200:
        errors = response.json().get("errors", []) if _is_json(response) else []
        codes = [e.get("code") for e in errors]
        # Never the detail: Square echoes request fields into it and this is the card path.
        log.warning("Square payment rejected: status=%s codes=%s", response.status_code, codes)
        for error in errors:
            if error.get("category") == "PAYMENT_METHOD_ERROR":
                code = str(error.get("code") or "")
                raise CardDeclined(DECLINE_MESSAGES.get(code, DECLINE_FALLBACK), code)
        raise SquareError(f"Square returned {response.status_code}")
    payment = response.json().get("payment") or {}
    if not payment.get("id") or not payment.get("status"):
        raise SquareError("Square response missing payment fields")
    money = payment.get("amount_money") or {}
    return PaymentResult(
        id=payment["id"],
        status=str(payment["status"]),
        amount_cents=money.get("amount") if isinstance(money.get("amount"), int) else 0,
        reference_id=payment.get("reference_id"),
        order_id=payment.get("order_id"),
        card_fingerprint=((payment.get("card_details") or {}).get("card") or {}).get("fingerprint"),
    )


def signature_for(raw_body: bytes, signature_key: str, notification_url: str) -> str:
    digest = hmac.new(signature_key.encode(), notification_url.encode() + raw_body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def signature_valid(raw_body: bytes, header: str | None, signature_key: str, notification_url: str) -> bool:
    if not header or not signature_key or not notification_url:
        return False
    expected = signature_for(raw_body, signature_key, notification_url)
    return hmac.compare_digest(expected.encode(), header.encode())


def confirmation_redirect_url(settings: Settings, hold: Hold) -> str:
    return f"{settings.frontend_origin}/confirm.html?hold={hold.id}"


def http_client(settings: Settings) -> httpx.Client:
    return httpx.Client(base_url=BASE_URLS[settings.square_environment], timeout=REQUEST_TIMEOUT_SECONDS)


def create_deposit_link(settings: Settings, hold: Hold, slot_starts_at_label: str) -> PaymentLink:
    """hold.id is the idempotency key, so retrying for the same hold returns the same link instead of a second one."""
    if settings.fake_square_enabled:
        return PaymentLink(
            id=f"dev-link-{hold.id}",
            url=f"{settings.frontend_origin}/dev-checkout.html?hold={hold.id}",
            order_id=f"dev-order-{hold.id}",
        )
    if not settings.square_access_token or not settings.square_location_id:
        raise SquareError("Square is not configured")
    body = {
        "idempotency_key": str(hold.id),
        "quick_pay": {
            "name": f"Party deposit: {PACKAGES[hold.package].label}, {slot_starts_at_label}",
            "price_money": {"amount": hold.deposit_cents, "currency": "USD"},
            "location_id": settings.square_location_id,
        },
        "checkout_options": {
            "redirect_url": confirmation_redirect_url(settings, hold),
            "allow_tipping": False,
            "ask_for_shipping_address": False,
        },
        "pre_populated_data": {"buyer_email": hold.email, "buyer_phone_number": hold.phone},
        "payment_note": f"Encore party hold {hold.id}",
    }
    headers = {
        "Authorization": f"Bearer {settings.square_access_token}",
        "Square-Version": SQUARE_VERSION,
        "Content-Type": "application/json",
    }
    try:
        with http_client(settings) as client:
            response = client.post("/v2/online-checkout/payment-links", json=body, headers=headers)
    except httpx.HTTPError as error:
        log.warning("Square payment link request failed: %s", type(error).__name__)
        raise SquareError("Square unreachable") from error
    if response.status_code != 200:
        codes = [e.get("code") for e in response.json().get("errors", [])] if _is_json(response) else []
        log.warning("Square payment link rejected: status=%s codes=%s", response.status_code, codes)
        raise SquareError(f"Square returned {response.status_code}")
    link = response.json().get("payment_link") or {}
    if not link.get("id") or not link.get("url") or not link.get("order_id"):
        raise SquareError("Square response missing payment link fields")
    return PaymentLink(id=link["id"], url=link["url"], order_id=link["order_id"])


def refund_payment(
    settings: Settings, payment_id: str, amount_cents: int, idempotency_key: str, reason: str
) -> RefundResult:
    """POST /v2/refunds, per Square's Refunds API (docs read 2026-10-02). The idempotency key is derived
    from the booking and capped at Square's 45 characters, so a retry or a double click refunds once.

    Square answers PENDING for a card refund it has accepted; the refund webhook settles it later.
    """
    if settings.fake_square_enabled:
        return RefundResult(id=f"dev-refund-{idempotency_key}", status="PENDING")
    if not settings.square_access_token:
        raise SquareError("Square is not configured")
    body = {
        "idempotency_key": idempotency_key[:SQUARE_IDEMPOTENCY_KEY_MAX],
        "payment_id": payment_id,
        "amount_money": {"amount": amount_cents, "currency": "USD"},
        "reason": reason[:SQUARE_REFUND_REASON_MAX],
    }
    headers = {
        "Authorization": f"Bearer {settings.square_access_token}",
        "Square-Version": SQUARE_VERSION,
        "Content-Type": "application/json",
    }
    try:
        with http_client(settings) as client:
            response = client.post("/v2/refunds", json=body, headers=headers)
    except httpx.HTTPError as error:
        log.warning("Square refund request failed: %s", type(error).__name__)
        raise SquareError("Square unreachable") from error
    if response.status_code != 200:
        codes = [e.get("code") for e in response.json().get("errors", [])] if _is_json(response) else []
        log.warning("Square refund rejected: status=%s codes=%s", response.status_code, codes)
        raise SquareError(f"Square returned {response.status_code}")
    refund = response.json().get("refund") or {}
    if not refund.get("id") or not refund.get("status"):
        raise SquareError("Square response missing refund fields")
    return RefundResult(id=refund["id"], status=refund["status"])


def _is_json(response: httpx.Response) -> bool:
    return response.headers.get("content-type", "").startswith("application/json")

