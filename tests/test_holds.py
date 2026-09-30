import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Hold
from tests.helpers import client, client_from, hold_body, make_booking, make_hold, make_slot, next_saturday


def test_hold_snapshots_server_price(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    response = client_from("192.0.2.1").post("/api/holds", json=hold_body(slot.id, 1, total_cents=1))
    assert response.status_code == 201
    body = response.json()
    assert 890 <= body["seconds_remaining"] <= 900
    hold = db.scalar(select(Hold).where(Hold.id == uuid.UUID(body["hold_id"])))
    assert hold is not None
    assert hold.total_cents == 54400
    assert hold.phone == "+19145550001"
    assert hold.email == "parent1@example.com"
    assert str(hold.client_ip) == "192.0.2.1"
    assert [item["key"] for item in hold.line_items] == ["package", "additional_kids", "pizza_pack"]


def test_expired_hold_on_slot_is_replaced(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    stale = make_hold(db, slot, expires_in=timedelta(minutes=-1))
    assert client_from("192.0.2.2").post("/api/holds", json=hold_body(slot.id, 1)).status_code == 201
    db.refresh(stale)
    assert stale.status == "expired"


def test_held_slot_returns_409_with_alternatives(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    make_hold(db, slot)
    response = client_from("192.0.2.3").post("/api/holds", json=hold_body(slot.id, 1))
    assert response.status_code == 409
    assert response.json()["alternatives"]


def test_booked_and_blocked_slots_return_409(db: Session) -> None:
    booked = make_slot(db, next_saturday(7))
    make_booking(db, make_hold(db, booked, status="booked"), booked)
    blocked = make_slot(db, next_saturday(14))
    blocked.blocked_at = blocked.created_at
    db.commit()
    requester = client_from("192.0.2.4")
    assert requester.post("/api/holds", json=hold_body(booked.id, 1)).status_code == 409
    assert requester.post("/api/holds", json=hold_body(blocked.id, 2)).status_code == 409


def test_invalid_inputs_are_422(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    requester = client_from("192.0.2.5")
    assert requester.post("/api/holds", json=hold_body(slot.id, 1, phone="555-1234")).status_code == 422
    assert requester.post("/api/holds", json=hold_body(slot.id, 1, email="not-an-email")).status_code == 422
    assert requester.post("/api/holds", json=hold_body(slot.id, 1, kids=31)).status_code == 422
    assert requester.post("/api/holds", json=hold_body(slot.id, 1, addons={"highlight_reel": 1})).status_code == 422
    assert requester.post("/api/holds", json=hold_body(999999, 1)).status_code == 404


def test_get_pending_hold(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    created = client_from("192.0.2.6").post("/api/holds", json=hold_body(slot.id, 1)).json()
    body = client().get(f"/api/holds/{created['hold_id']}").json()
    assert body["status"] == "pending_payment"
    assert body["expires_at"] == created["expires_at"]
    assert body["seconds_remaining"] > 0
    assert body["party"]["balance_cents"] == 44400
    assert body["party"]["confirmation_code"] is None
    assert body["party"]["first_name"] is None


def test_get_lapsed_hold_reads_expired(db: Session) -> None:
    hold = make_hold(db, make_slot(db, next_saturday(7)), expires_in=timedelta(seconds=-5))
    body = client().get(f"/api/holds/{hold.id}").json()
    assert body["status"] == "expired"
    assert body["seconds_remaining"] == 0
    assert body["checkout_url"] is None


def test_get_booked_hold_exposes_no_contact_details(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    hold = make_hold(db, slot, status="booked")
    booking = make_booking(db, hold, slot)
    response = client().get(f"/api/holds/{hold.id}")
    body = response.json()
    assert body["status"] == "booked"
    assert body["party"]["confirmation_code"] == booking.confirmation_code
    assert body["party"]["first_name"] == "Pat"
    for secret in (hold.phone, hold.email, hold.last_name):
        assert secret not in response.text


def test_unknown_hold_is_404() -> None:
    assert client().get(f"/api/holds/{uuid.uuid4()}").status_code == 404
    assert client().get("/api/holds/not-a-uuid").status_code == 404
