import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.square import SQUARE_VERSION
from tests.conftest import FakeSquareApi
from tests.helpers import checkout, client, client_from, make_slot, next_saturday, start_hold


def test_payment_link_uses_hold_id_as_idempotency_key(db: Session, engine: Engine, square_api: FakeSquareApi) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.60")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    response = checkout(funnel, hold_id, 1)
    assert response.status_code == 200
    assert response.json()["checkout_url"].startswith("https://sandbox.square.link/")

    (sent,) = square_api.requests
    assert sent["path"] == "/v2/online-checkout/payment-links"
    headers = sent["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer test-square-token"
    assert headers["square-version"] == SQUARE_VERSION
    body = sent["body"]
    assert isinstance(body, dict)
    assert body["idempotency_key"] == hold_id
    assert body["quick_pay"]["price_money"] == {"amount": 10000, "currency": "USD"}
    assert body["quick_pay"]["location_id"] == "LTESTLOCATION"
    assert body["checkout_options"]["redirect_url"] == f"http://testserver/confirm.html?hold={hold_id}"
    assert body["pre_populated_data"] == {"buyer_email": "parent1@example.com", "buyer_phone_number": "+19145550001"}

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT square_order_id, square_payment_link_id, checkout_url FROM holds WHERE id = :id"), {"id": hold_id}
        ).one()
    assert row.square_order_id == f"order-{hold_id}"
    assert row.square_payment_link_id == f"link-{hold_id}"


def test_square_failure_keeps_the_hold_for_a_retry(db: Session, engine: Engine, square_api: FakeSquareApi) -> None:
    """The parent is at the pay button: their date is not released over a Square blip."""
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.61")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    square_api.fail_with = 500
    response = checkout(funnel, hold_id, 1)
    assert response.status_code == 502
    assert "still held" in response.json()["detail"]
    with engine.connect() as conn:
        row = conn.execute(text("SELECT status, square_order_id, total_cents FROM holds")).one()
    assert row.status == "pending_payment" and row.square_order_id is None
    assert row.total_cents == 54400, "the snapshot committed; only the link is missing"
    square_api.fail_with = None
    retry = checkout(funnel, hold_id, 1)
    assert retry.status_code == 200, "the same hold checks out once Square answers"


def test_booking_lookup_by_code(db: Session) -> None:
    from tests.helpers import create_hold_via_api, deliver, payment_event

    hold_id, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]
    response = client().get(f"/api/bookings/{code.lower()}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "booked"
    assert body["party"]["balance_cents"] == 44400
    assert "+1914" not in response.text and "@example.com" not in response.text
    assert client().get("/api/bookings/AAAAAAAAAA").status_code == 404
    assert client().get("/api/bookings/short").status_code == 404


def test_dev_fake_payment_is_off_by_default() -> None:
    assert client().post(f"/api/dev/fake-payment/{uuid.uuid4()}").status_code == 404


def test_dev_fake_payment_runs_the_real_webhook_path(
    db: Session, monkeypatch: pytest.MonkeyPatch, square_api: FakeSquareApi
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "square_access_token", "")
    monkeypatch.setattr(settings, "dev_fake_square", True)
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.62")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    paid = checkout(funnel, hold_id, 1).json()
    assert paid["checkout_url"] == f"http://testserver/dev-checkout.html?hold={hold_id}"
    assert square_api.requests == []
    assert client().post(f"/api/dev/fake-payment/{hold_id}").json() == {"outcome": "booked"}
    assert client().get(f"/api/holds/{hold_id}").json()["status"] == "booked"


def test_dev_fake_payment_refuses_when_real_square_is_configured(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "dev_fake_square", True)
    funnel = client_from("203.0.113.63")
    hold_id = start_hold(funnel, make_slot(db, next_saturday(7)).id).json()["hold_id"]
    checkout(funnel, hold_id, 1)
    assert client().post(f"/api/dev/fake-payment/{hold_id}").status_code == 404
