import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Hold
from tests.conftest import FakeSquareApi
from tests.helpers import (
    checkout,
    client,
    client_from,
    make_booking,
    make_hold,
    make_slot,
    next_saturday,
    start_hold,
)


def test_tapping_a_date_holds_the_slot_anonymously(db: Session, square_api: FakeSquareApi) -> None:
    slot = make_slot(db, next_saturday(7))
    response = start_hold(client_from("192.0.2.1"), slot.id)
    assert response.status_code == 201
    body = response.json()
    assert 890 <= body["seconds_remaining"] <= 900
    assert body["time_label"] == "10:00 AM"
    hold = db.scalar(select(Hold).where(Hold.id == uuid.UUID(body["hold_id"])))
    assert hold is not None
    assert hold.package is None and hold.phone is None and hold.email is None
    assert hold.deposit_cents == 10000
    assert str(hold.client_ip) == "192.0.2.1"
    assert hold.square_order_id is None, "no payment link until checkout"
    assert square_api.requests == []


def test_checkout_snapshots_server_price(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("192.0.2.10")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    response = checkout(funnel, hold_id, 1, total_cents=1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["checkout_url"].startswith("https://sandbox.square.link/")
    assert body["party"]["total_cents"] == 54400
    hold = db.scalar(select(Hold).where(Hold.id == uuid.UUID(hold_id)))
    assert hold is not None
    assert hold.total_cents == 54400
    assert hold.phone == "+19145550001"
    assert hold.email == "parent1@example.com"
    assert hold.kid_name == "Sam" and hold.kid_age == 8
    assert [item["key"] for item in hold.line_items] == ["package", "additional_kids", "pizza_pack"]


def test_second_checkout_reprices_and_reuses_the_payment_link(db: Session, square_api: FakeSquareApi) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("192.0.2.11")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    first = checkout(funnel, hold_id, 1)
    second = checkout(funnel, hold_id, 1, addons={})
    assert second.status_code == 200
    assert second.json()["party"]["total_cents"] == 50400, "the new snapshot drops the pizza pack"
    assert second.json()["checkout_url"] == first.json()["checkout_url"]
    assert len(square_api.requests) == 1, "the $100 link is reused, never recreated"


def test_checkout_on_expired_hold_changes_nothing(db: Session) -> None:
    hold = make_hold(db, make_slot(db, next_saturday(7)), expires_in=timedelta(minutes=-1))
    response = checkout(client(), str(hold.id), 1)
    assert response.status_code == 409
    assert response.json()["status"] == "expired"
    db.refresh(hold)
    assert hold.square_order_id is None


def test_checkout_on_cancelled_hold_is_409(db: Session) -> None:
    hold = make_hold(db, make_slot(db, next_saturday(7)), status="cancelled")
    response = checkout(client(), str(hold.id), 1)
    assert response.status_code == 409
    assert response.json()["status"] == "cancelled"


def test_tapping_a_new_date_swaps_the_hold(db: Session) -> None:
    saturday = make_slot(db, next_saturday(7))
    sunday = make_slot(db, next_saturday(7) + timedelta(days=1))
    funnel = client_from("192.0.2.12")
    first = start_hold(funnel, saturday.id).json()["hold_id"]
    second = start_hold(funnel, sunday.id, replace_hold_id=first)
    assert second.status_code == 201
    old = db.scalar(select(Hold).where(Hold.id == uuid.UUID(first)))
    assert old is not None and old.status == "cancelled"
    assert start_hold(client_from("192.0.2.13"), saturday.id).status_code == 201, "the first date is free again"


def test_email_saved_on_blur_for_abandoner_recovery(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    hold_id = start_hold(client_from("192.0.2.14"), slot.id).json()["hold_id"]
    response = client().patch(f"/api/holds/{hold_id}/email", json={"email": "Abandoner@Example.com"})
    assert response.status_code == 200 and response.json() == {"saved": True}
    hold = db.scalar(select(Hold).where(Hold.id == uuid.UUID(hold_id)))
    assert hold is not None and hold.email == "abandoner@example.com"
    assert client().patch(f"/api/holds/{uuid.uuid4()}/email", json={"email": "a@b.com"}).json() == {"saved": False}


def test_expired_hold_on_slot_is_replaced(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    stale = make_hold(db, slot, expires_in=timedelta(minutes=-1))
    assert start_hold(client_from("192.0.2.2"), slot.id).status_code == 201
    db.refresh(stale)
    assert stale.status == "expired"


def test_held_slot_returns_409_with_alternatives(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    make_hold(db, slot)
    response = start_hold(client_from("192.0.2.3"), slot.id)
    assert response.status_code == 409
    assert response.json()["alternatives"]


def test_booked_and_blocked_slots_return_409(db: Session) -> None:
    booked = make_slot(db, next_saturday(7))
    make_booking(db, make_hold(db, booked, status="booked"), booked)
    blocked = make_slot(db, next_saturday(14))
    blocked.blocked_at = blocked.created_at
    db.commit()
    requester = client_from("192.0.2.4")
    assert start_hold(requester, booked.id).status_code == 409
    assert start_hold(requester, blocked.id).status_code == 409


def test_invalid_checkout_inputs_are_422(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("192.0.2.5")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    assert checkout(funnel, hold_id, 1, phone="555-1234").status_code == 422
    assert checkout(funnel, hold_id, 1, email="not-an-email").status_code == 422
    assert checkout(funnel, hold_id, 1, kids=31).status_code == 422
    assert checkout(funnel, hold_id, 1, addons={"highlight_reel": 1}).status_code == 422
    assert checkout(funnel, hold_id, 1, kid_name="").status_code == 422
    assert start_hold(funnel, 999999).status_code == 404
    hold = db.scalar(select(Hold).where(Hold.id == uuid.UUID(hold_id)))
    assert hold is not None and hold.package is None, "a rejected checkout snapshots nothing"


def test_get_hold_before_checkout_has_slot_but_no_party(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    created = start_hold(client_from("192.0.2.6"), slot.id).json()
    body = client().get(f"/api/holds/{created['hold_id']}").json()
    assert body["status"] == "pending_payment"
    assert body["expires_at"] == created["expires_at"]
    assert body["party"] is None
    assert body["checkout_url"] is None
    assert body["slot"]["time_label"] == "10:00 AM"
    assert body["slot"]["date"] == created["date"]


def test_get_hold_after_checkout_has_the_party(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("192.0.2.7")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    checkout(funnel, hold_id, 1)
    body = client().get(f"/api/holds/{hold_id}").json()
    assert body["status"] == "pending_payment"
    assert body["checkout_url"] is not None
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


def _grid_status(day: date) -> str:
    days = client().get("/api/availability").json()["days"]
    return next(entry["status"] for entry in days if entry["date"] == day.isoformat())


def test_lapsed_hold_frees_the_date_on_the_grid(db: Session) -> None:
    """What the next parent sees: a hold that ran out stops holding the date."""
    day = next_saturday(7)
    slot = make_slot(db, day)
    hold = make_hold(db, slot)
    assert _grid_status(day) == "held"
    hold.expires_at = hold.expires_at - timedelta(minutes=16)
    db.commit()
    assert _grid_status(day) == "open"


def test_swapping_dates_frees_the_one_left_behind(db: Session) -> None:
    saturday = next_saturday(7)
    sunday = saturday + timedelta(days=1)
    first_slot = make_slot(db, saturday)
    second_slot = make_slot(db, sunday)
    funnel = client_from("192.0.2.14")
    first = start_hold(funnel, first_slot.id).json()["hold_id"]
    assert _grid_status(saturday) == "held"
    assert start_hold(funnel, second_slot.id, replace_hold_id=first).status_code == 201
    assert _grid_status(saturday) == "open"
    assert _grid_status(sunday) == "held"
