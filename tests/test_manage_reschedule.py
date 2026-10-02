"""Moving a party to another date: the same slot exclusivity as a first booking, and no money moving."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Booking, Refund, Slot
from tests.conftest import FakeSquareApi
from tests.helpers import client, client_from, make_booking, make_hold, make_slot, start_hold, today, weekend_near


def _booking_on(db: Session, days_away: int) -> Booking:
    slot = make_slot(db, weekend_near(days_away))
    return make_booking(db, make_hold(db, slot), slot)


def _grid_status(day) -> str:
    body = client().get("/api/availability?weekends=52").json()
    return next(entry["status"] for entry in body["days"] if entry["date"] == day.isoformat())


def test_a_move_swaps_the_date_on_the_grid(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    old_day = booking.event_starts_at.date()
    target = make_slot(db, weekend_near(45))

    response = client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})
    assert response.status_code == 200, response.text
    assert _grid_status(old_day) == "open"
    assert _grid_status(target.local_date) == "booked"


def test_the_hold_moves_with_the_booking(db: Session) -> None:
    """Both rows carry the slot and both feed the exclusivity indexes, so a hold left behind would keep the
    old date locked and leave the new one claimable by someone else."""
    booking = _booking_on(db, days_away=30)
    target = make_slot(db, weekend_near(45))
    client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})

    hold_slot = db.execute(text("SELECT slot_id FROM holds WHERE id = :id"), {"id": booking.hold_id}).scalar_one()
    db.refresh(booking)
    assert hold_slot == target.id
    assert booking.slot_id == target.id
    assert booking.event_starts_at == target.starts_at


def test_no_money_moves_on_a_reschedule(db: Session, square_api: FakeSquareApi) -> None:
    booking = _booking_on(db, days_away=30)
    paid_before = (booking.paid_amount_cents, booking.deposit_cents, booking.square_payment_id)
    target = make_slot(db, weekend_near(45))
    client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})

    db.refresh(booking)
    assert (booking.paid_amount_cents, booking.deposit_cents, booking.square_payment_id) == paid_before
    assert square_api.requests == []
    assert db.query(Refund).count() == 0


def test_a_date_someone_else_holds_is_refused_and_changes_nothing(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    target = make_slot(db, weekend_near(45))
    assert start_hold(client_from("203.0.113.7"), target.id).status_code == 201

    response = client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})
    assert response.status_code == 409
    assert "has not moved" in response.json()["detail"]
    db.refresh(booking)
    assert booking.slot_id != target.id
    assert _grid_status(booking.event_starts_at.date()) == "booked"


def test_a_date_someone_else_booked_is_refused(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    other = _booking_on(db, days_away=45)
    response = client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": other.slot_id})
    assert response.status_code == 409
    db.refresh(booking)
    assert booking.slot_id != other.slot_id


def test_a_lapsed_hold_does_not_block_a_move(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    target = make_slot(db, weekend_near(45))
    make_hold(db, target, expires_in=timedelta(minutes=-1))
    response = client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})
    assert response.status_code == 200, response.text


def test_a_date_inside_the_lead_time_window_is_refused(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lead time has to be what refuses it, so the date is a real weekend and the window is widened to
    reach it: on most days of the week there is no weekend day inside the default two."""
    monkeypatch.setattr(get_settings(), "min_lead_days", 14)
    booking = _booking_on(db, days_away=30)
    target = make_slot(db, weekend_near(3))
    response = client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})
    assert response.status_code == 422
    assert "too soon" in response.json()["detail"]


def test_a_blocked_date_is_refused(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    target = make_slot(db, weekend_near(45))
    db.execute(text("UPDATE slots SET blocked_at = now() WHERE id = :id"), {"id": target.id})
    db.commit()
    assert client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id}).status_code == 409


def test_moving_to_the_same_date_is_refused(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    response = client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": booking.slot_id})
    assert response.status_code == 422
    assert "already your party date" in response.json()["detail"]


def test_outside_the_window_moves_are_free_and_unlimited(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    for offset in (45, 60, 75):
        target = make_slot(db, weekend_near(offset))
        response = client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})
        assert response.status_code == 200, response.text
    db.refresh(booking)
    assert booking.reschedule_count == 3
    # None of them spent the courtesy move, because none of them was made inside the window.
    assert booking.courtesy_reschedule_used is False


def test_inside_the_window_allows_exactly_one_move(db: Session) -> None:
    booking = _booking_on(db, days_away=3)
    first = make_slot(db, weekend_near(45))
    assert client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": first.id}).status_code == 200

    db.refresh(booking)
    assert booking.courtesy_reschedule_used is True


def test_the_second_move_inside_the_window_is_refused(db: Session) -> None:
    booking = _booking_on(db, days_away=3)
    booking.courtesy_reschedule_used = True
    db.commit()
    target = make_slot(db, weekend_near(45))
    response = client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})
    assert response.status_code == 409
    assert "courtesy move has been used" in response.json()["detail"]


def test_eligibility_follows_the_new_date_not_the_old_one(db: Session) -> None:
    """A party moved far out is judged by where it now sits, so it is free to move again."""
    booking = _booking_on(db, days_away=3)
    far = make_slot(db, weekend_near(60))
    client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": far.id})

    body = client().get(f"/api/manage/{booking.manage_token}").json()
    assert body["inside_free_window"] is True
    assert body["can"]["can_reschedule"] is True
    assert body["can"]["refund_on_cancel"] is True

    again = make_slot(db, weekend_near(75))
    assert client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": again.id}).status_code == 200


def test_a_cancelled_booking_cannot_be_moved(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    target = make_slot(db, weekend_near(45))
    assert client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id}).status_code == 409


def test_the_owner_is_told_where_the_party_went(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    target = make_slot(db, weekend_near(45))
    client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})
    rows = db.execute(text("SELECT body FROM notifications WHERE event = 'rescheduled'")).all()
    assert len(rows) == 1
    assert "MOVED" in rows[0].body
    assert "Deposit rides along" in rows[0].body


def test_an_unknown_slot_is_refused(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    missing = db.query(Slot).count() + 1000
    assert client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": missing}).status_code == 422
