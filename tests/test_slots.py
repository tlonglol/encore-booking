from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Slot
from app.config import get_settings
from app.services.slots import availability
from app.timeutil import upcoming_weekend_days
from tests.helpers import admin_client, client, make_booking, make_hold, make_slot, next_saturday


def test_weekend_days_start_with_the_coming_weekend() -> None:
    days = upcoming_weekend_days(date(2026, 9, 30), 8)  # a Wednesday
    assert days[0] == date(2026, 10, 3)
    assert len(days) == 16
    assert {day.weekday() for day in days} == {5, 6}


def test_weekend_in_progress_counts_as_the_first() -> None:
    days = upcoming_weekend_days(date(2026, 10, 4), 8)  # a Sunday
    assert days[0] == date(2026, 10, 4)
    assert len(days) == 15


def test_slots_are_10am_new_york_across_dst(db: Session) -> None:
    views = availability(db, date(2026, 10, 28), 2, 2)
    by_date = {view.local_date: view.starts_at.astimezone(UTC) for view in views if view.starts_at}
    assert by_date[date(2026, 10, 31)] == datetime(2026, 10, 31, 14, 0, tzinfo=UTC)  # EDT
    assert by_date[date(2026, 11, 7)] == datetime(2026, 11, 7, 15, 0, tzinfo=UTC)  # EST


def test_generator_is_idempotent(db: Session) -> None:
    availability(db, date(2026, 9, 30), 2, 8)
    availability(db, date(2026, 9, 30), 2, 8)
    assert db.scalar(select(func.count()).select_from(Slot)) == 16


def test_lead_time_is_calendar_days(db: Session) -> None:
    thursday, friday = date(2026, 10, 1), date(2026, 10, 2)
    on_thursday = {view.local_date: view.status for view in availability(db, thursday, 2, 1)}
    on_friday = {view.local_date: view.status for view in availability(db, friday, 2, 1)}
    assert on_thursday[date(2026, 10, 3)] == "open"
    assert on_friday[date(2026, 10, 3)] == "past"
    assert on_friday[date(2026, 10, 4)] == "open"


def test_statuses_are_computed(db: Session) -> None:
    today = date(2026, 9, 30)
    days = upcoming_weekend_days(today, 3)
    slots = {day: make_slot(db, day) for day in days[:5]}
    make_hold(db, slots[days[0]])
    make_hold(db, slots[days[1]], expires_in=timedelta(minutes=-1))
    make_booking(db, make_hold(db, slots[days[2]], status="booked"), slots[days[2]])
    slots[days[3]].blocked_at = datetime.now(UTC)
    db.commit()
    statuses = {view.local_date: view.status for view in availability(db, today, 2, 3)}
    assert statuses[days[0]] == "held"
    assert statuses[days[1]] == "open"  # expired hold frees the slot
    assert statuses[days[2]] == "booked"
    assert statuses[days[3]] == "blocked"
    assert statuses[days[4]] == "open"


def test_availability_endpoint() -> None:
    response = client().get("/api/availability")
    assert response.status_code == 200
    body = response.json()
    assert body["weekend_default"] == "open"
    days = body["days"]
    assert len(days) >= 50, "every date through the last weekend, not just weekends"
    slot_days = [d for d in days if d["slot_id"] is not None]
    weekday_days = [d for d in days if d["slot_id"] is None]
    assert len(slot_days) >= 15
    assert all(d["time_label"] == "10:00 AM" for d in slot_days)
    assert all(d["time_label"] is None and d["status"] in {"request", "past"} for d in weekday_days)
    assert any(d["status"] == "request" for d in weekday_days)
    assert set(days[0]) == {"date", "day_label", "status", "slot_id", "time_label", "starts_at"}


def test_admin_requires_login(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    anonymous = client()
    assert anonymous.post(f"/api/admin/slots/{slot.id}/block").status_code == 401
    assert anonymous.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    anonymous.cookies.set("encore_admin", "9999999999.forged", domain="testserver", path="/api/admin")
    assert anonymous.post(f"/api/admin/slots/{slot.id}/block").status_code == 401


def test_admin_block_and_unblock(db: Session) -> None:
    day = next_saturday(7)
    slot = make_slot(db, day)
    admin = admin_client()
    assert admin.post(f"/api/admin/slots/{slot.id}/block").json() == {"id": slot.id, "blocked": True}
    statuses = {s["date"]: s["status"] for s in client().get("/api/availability").json()["days"]}
    assert statuses[day.isoformat()] == "blocked"
    assert admin.post(f"/api/admin/slots/{slot.id}/unblock").json() == {"id": slot.id, "blocked": False}
    assert admin.post("/api/admin/slots/999999/block").status_code == 404


def test_blocking_a_held_or_booked_slot_is_refused(db: Session) -> None:
    held = make_slot(db, next_saturday(7))
    booked = make_slot(db, next_saturday(14))
    make_hold(db, held)
    make_booking(db, make_hold(db, booked, status="booked"), booked)
    admin = admin_client()
    assert admin.post(f"/api/admin/slots/{held.id}/block").status_code == 409
    assert admin.post(f"/api/admin/slots/{booked.id}/block").status_code == 409


def test_blocking_a_slot_with_only_an_expired_hold_is_allowed(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    make_hold(db, slot, expires_in=timedelta(minutes=-1))
    assert admin_client().post(f"/api/admin/slots/{slot.id}/block").status_code == 200


def test_weekends_are_request_only_in_request_mode(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "weekend_default", "request")
    body = client().get("/api/availability").json()
    assert body["weekend_default"] == "request"
    slot_days = [d for d in body["days"] if d["slot_id"] is not None]
    assert slot_days
    assert {d["status"] for d in slot_days} <= {"request", "past", "held", "booked", "blocked"}
    assert not any(d["status"] == "open" for d in body["days"])
