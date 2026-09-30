import secrets
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import Booking, Hold, Slot
from app.pricing import quote
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
        confirmation_code=secrets.token_hex(5).upper(),
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
        event_starts_at=slot.starts_at,
    )
    db.add(booking)
    db.commit()
    return booking


def hold_body(slot_id: int, n: int = 0, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "slot_id": slot_id,
        "package": "standard",
        "kids": 18,
        "addons": {"pizza_pack": 1},
        "first_name": "Pat",
        "last_name": f"Parent{n}",
        "phone": f"(914) 555-{n:04d}",
        "email": f"parent{n}@example.com",
        "notes": "Nut allergy at one guest",
    }
    body.update(overrides)
    return body


def client_from(ip: str) -> TestClient:
    return TestClient(app, base_url="https://testserver", client=(ip, 50000))


WEBHOOK_KEY = "test-signature-key"
WEBHOOK_URL = "https://encore-test.example.com/api/webhooks/square"


def payment_event(
    order_id: str,
    payment_id: str = "pay-1",
    event_id: str | None = None,
    status: str = "COMPLETED",
    amount: int = 10000,
    currency: str = "USD",
    event_type: str = "payment.updated",
) -> bytes:
    import json

    return json.dumps(
        {
            "merchant_id": "MTEST",
            "type": event_type,
            "event_id": event_id or str(uuid.uuid4()),
            "created_at": "2026-10-03T14:00:00Z",
            "data": {
                "type": "payment",
                "id": payment_id,
                "object": {
                    "payment": {
                        "id": payment_id,
                        "status": status,
                        "amount_money": {"amount": amount, "currency": currency},
                        "order_id": order_id,
                    }
                },
            },
        }
    ).encode()


def deliver(raw: bytes, key: str = WEBHOOK_KEY, url: str = WEBHOOK_URL, signature: str | None = None):  # type: ignore[no-untyped-def]
    from app.services.square import signature_for

    headers = {"Content-Type": "application/json"}
    headers["x-square-hmacsha256-signature"] = signature if signature is not None else signature_for(raw, key, url)
    return client().post("/api/webhooks/square", content=raw, headers=headers)


def create_hold_via_api(db: Session, days_ahead: int = 7, n: int = 1) -> tuple[str, str]:
    """Returns (hold_id, square_order_id) for a fresh pending hold created through the real endpoint."""
    slot = make_slot(db, next_saturday(days_ahead))
    response = client_from(f"203.0.113.{100 + n}").post("/api/holds", json=hold_body(slot.id, n))
    assert response.status_code == 201, response.text
    hold_id = response.json()["hold_id"]
    return hold_id, f"order-{hold_id}"
