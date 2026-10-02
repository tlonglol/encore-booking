import secrets
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import Booking, Hold, Slot
from app.pricing import quote
from app.services.booking import new_confirmation_code, new_manage_token
from app.timeutil import slot_start_utc, venue_today

ADMIN_PASSWORD = "test-admin-password"


def client() -> TestClient:
    # https so the Secure admin cookie round-trips.
    return TestClient(app, base_url="https://testserver")


def admin_client() -> TestClient:
    test_client = client()
    response = test_client.post("/api/admin/login", json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return test_client


def today() -> date:
    return venue_today(datetime.now(UTC))


def next_saturday(min_days_ahead: int) -> date:
    day = today() + timedelta(days=min_days_ahead)
    return day + timedelta(days=(5 - day.weekday()) % 7)


def next_tuesday(min_days_ahead: int) -> date:
    """A weekday the parents' grid only ever shows as a request date, and the owner can still book."""
    day = today() + timedelta(days=min_days_ahead)
    return day + timedelta(days=(1 - day.weekday()) % 7)


def weekend_near(days_ahead: int) -> date:
    """The weekend day closest to roughly that far out, and never today or earlier.

    A fixture that says "a party about a month away" has to land on a weekend, because every parent-facing
    surface treats a weekday date as a request date with no slot: the grid will not show one and a hold or
    a move will not take one. Nearest rather than next, so the distance a test asked for mostly survives:
    at most four days off, which keeps a date asked for inside the one-week refund window inside it. A
    distance that has to be exact (the 48-hour add-on cutoff) cannot be expressed as a weekend at all, and
    those tests keep their own weekday dates: nothing parent-facing reads them.
    """
    target = today() + timedelta(days=days_ahead)
    if target.weekday() >= 5:
        return target
    forward = (5 - target.weekday()) % 7
    backward = (target.weekday() - 6) % 7
    if target - timedelta(days=backward) <= today() or forward <= backward:
        return target + timedelta(days=forward)
    return target - timedelta(days=backward)


def make_slot(db: Session, day: date) -> Slot:
    slot = Slot(starts_at=slot_start_utc(day), local_date=day)
    db.add(slot)
    db.commit()
    return slot


def make_hold(
    db: Session,
    slot: Slot,
    status: str = "pending_payment",
    expires_in: timedelta = timedelta(minutes=15),
    phone: str | None = None,
    order_id: str | None = None,
) -> Hold:
    priced = quote("standard", 18, {"pizza_pack": 1})
    tag = secrets.token_hex(4)
    hold = Hold(
        id=uuid.uuid4(),
        slot_id=slot.id,
        status=status,
        expires_at=datetime.now(UTC) + expires_in,
        package=priced.package,
        kids=priced.kids,
        addons=priced.addons,
        line_items=priced.line_items_json(),
        total_cents=priced.total_cents,
        deposit_cents=priced.deposit_cents,
        first_name="Pat",
        last_name="Parent",
        phone=phone or f"+1914555{secrets.randbelow(10_000):04d}",
        email=f"parent-{tag}@example.com",
        square_order_id=order_id,
    )
    db.add(hold)
    db.commit()
    return hold


def make_booking(db: Session, hold: Hold, slot: Slot) -> Booking:
    booking = Booking(
        hold_id=hold.id,
        slot_id=slot.id,
        confirmation_code=new_confirmation_code(),
        manage_token=new_manage_token(),
        package=hold.package,
        kids=hold.kids,
        addons=hold.addons,
        line_items=hold.line_items,
        total_cents=hold.total_cents,
        deposit_cents=hold.deposit_cents,
        first_name=hold.first_name,
        last_name=hold.last_name,
        phone=hold.phone,
        email=hold.email,
        square_payment_id=f"pay-{secrets.token_hex(4)}",
        square_order_id=hold.square_order_id or f"order-{secrets.token_hex(4)}",
        paid_amount_cents=10000,
        kid_name=hold.kid_name,
        kid_age=hold.kid_age,
        event_starts_at=slot.starts_at,
    )
    db.add(booking)
    db.commit()
    return booking


def checkout_body(n: int = 0, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "package": "standard",
        "kids": 18,
        "addons": {"pizza_pack": 1},
        "first_name": "Pat",
        "last_name": f"Parent{n}",
        "phone": f"(914) 555-{n:04d}",
        "email": f"parent{n}@example.com",
        "kid_name": "Sam",
        "kid_age": 8,
    }
    body.update(overrides)
    return body


def start_hold(test_client: TestClient, slot_id: int, replace_hold_id: str | None = None):  # type: ignore[no-untyped-def]
    """Page-1 click: the anonymous hold that the funnel creates when a date card is tapped."""
    body: dict[str, object] = {"slot_id": slot_id}
    if replace_hold_id is not None:
        body["replace_hold_id"] = replace_hold_id
    return test_client.post("/api/holds", json=body)


def checkout(test_client: TestClient, hold_id: str, n: int = 0, **overrides: object):  # type: ignore[no-untyped-def]
    """Page-4 click: contact details, server pricing snapshot, and the payment link."""
    return test_client.post(f"/api/holds/{hold_id}/checkout", json=checkout_body(n, **overrides))


def client_from(ip: str) -> TestClient:
    return TestClient(app, base_url="https://testserver", client=(ip, 50000))


def browser_from(ip: str) -> TestClient:
    """A separate browser at the same address. Each TestClient keeps its own cookie jar, so each one gets
    its own anonymous session, which is what several people behind one mobile network look like."""
    return client_from(ip)


WEBHOOK_KEY = "test-signature-key"
WEBHOOK_URL = "https://encore-test.example.com/api/webhooks/square"


def payment_event(
    reference_id: str,
    payment_id: str = "pay-1",
    event_id: str | None = None,
    status: str = "COMPLETED",
    amount: int = 10000,
    currency: str = "USD",
    event_type: str = "payment.updated",
    order_id: str | None = None,
) -> bytes:
    """A Square payment webhook.

    The first argument is how the payment finds its hold. Since the deposit is taken through the
    Payments API there is no order, so that linkage is `reference_id`, which the pay endpoint sets to
    the hold id. `order_id` stays available for the one test that still covers the old payment-link
    shape, where an order was the only linkage there was.
    """
    import json

    payment: dict[str, object] = {
        "id": payment_id,
        "status": status,
        "amount_money": {"amount": amount, "currency": currency},
        "order_id": order_id,
    }
    if reference_id is not None:
        payment["reference_id"] = reference_id
    return json.dumps(
        {
            "merchant_id": "MTEST",
            "type": event_type,
            "event_id": event_id or str(uuid.uuid4()),
            "created_at": "2026-10-03T14:00:00Z",
            "data": {"type": "payment", "id": payment_id, "object": {"payment": payment}},
        }
    ).encode()


def deliver(raw: bytes, key: str = WEBHOOK_KEY, url: str = WEBHOOK_URL, signature: str | None = None):  # type: ignore[no-untyped-def]
    from app.services.square import signature_for

    headers = {"Content-Type": "application/json"}
    headers["x-square-hmacsha256-signature"] = signature if signature is not None else signature_for(raw, key, url)
    return client().post("/api/webhooks/square", content=raw, headers=headers)


def create_hold_via_api(db: Session, days_ahead: int = 7, n: int = 1) -> tuple[str, str]:
    """Returns (hold_id, reference_id) for a fresh checked-out hold created through the real funnel.

    Both values are the hold id: the pay endpoint sends it to Square as the payment's reference_id, so
    it is also what a webhook for that payment carries back. They are returned as a pair because the
    callers read very differently when one is the thing under test and the other is the linkage.
    """
    slot = make_slot(db, next_saturday(days_ahead))
    funnel_client = client_from(f"203.0.113.{100 + n}")
    created = start_hold(funnel_client, slot.id)
    assert created.status_code == 201, created.text
    hold_id = created.json()["hold_id"]
    paid = checkout(funnel_client, hold_id, n)
    assert paid.status_code == 200, paid.text
    return hold_id, hold_id
