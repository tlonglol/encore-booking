import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.square import SQUARE_VERSION
from tests.conftest import FakeSquareApi
from tests.helpers import client, client_from, hold_body, make_slot, next_saturday


def test_payment_link_uses_hold_id_as_idempotency_key(db: Session, engine: Engine, square_api: FakeSquareApi) -> None:
    slot = make_slot(db, next_saturday(7))
    response = client_from("203.0.113.60").post("/api/holds", json=hold_body(slot.id, 1))
    assert response.status_code == 201
    hold_id = response.json()["hold_id"]
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


def test_square_failure_releases_the_slot(db: Session, engine: Engine, square_api: FakeSquareApi) -> None:
    slot = make_slot(db, next_saturday(7))
    square_api.fail_with = 500
    response = client_from("203.0.113.61").post("/api/holds", json=hold_body(slot.id, 1))
    assert response.status_code == 502
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM holds")).scalar_one() == "cancelled"
    square_api.fail_with = None
    retry = client_from("203.0.113.61").post("/api/holds", json=hold_body(slot.id, 1))
    assert retry.status_code == 201, "the cancelled hold neither occupies the slot nor counts toward caps"


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
    created = client_from("203.0.113.62").post("/api/holds", json=hold_body(slot.id, 1)).json()
    assert created["checkout_url"] == f"http://testserver/dev-checkout.html?hold={created['hold_id']}"
    assert square_api.requests == []
    assert client().post(f"/api/dev/fake-payment/{created['hold_id']}").json() == {"outcome": "booked"}
    assert client().get(f"/api/holds/{created['hold_id']}").json()["status"] == "booked"


def test_dev_fake_payment_refuses_when_real_square_is_configured(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "dev_fake_square", True)
    hold_id = client_from("203.0.113.63").post("/api/holds", json=hold_body(make_slot(db, next_saturday(7)).id, 1)).json()["hold_id"]
    assert client().post(f"/api/dev/fake-payment/{hold_id}").status_code == 404
