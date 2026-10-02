from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config import get_settings

from tests.helpers import (
    admin_client,
    checkout,
    client,
    client_from,
    create_hold_via_api,
    deliver,
    make_slot,
    next_saturday,
    payment_event,
    start_hold,
    today,
)


def _draft(test_client, hold_id: str, birthday: object):  # type: ignore[no-untyped-def]
    return test_client.patch(
        f"/api/holds/{hold_id}/draft",
        json={"package": "standard", "kids": 18, "addons": {}, "birthday": birthday},
    )


def _new_hold(db: Session, ip: str):  # type: ignore[no-untyped-def]
    funnel = client_from(ip)
    return funnel, start_hold(funnel, make_slot(db, next_saturday(7)).id).json()["hold_id"]


def test_birthday_saves_on_the_draft_and_can_be_cleared(db: Session, engine: Engine) -> None:
    funnel, hold_id = _new_hold(db, "203.0.113.180")
    day = (today() + timedelta(days=10)).isoformat()
    assert _draft(funnel, hold_id, day).status_code == 200
    with engine.connect() as conn:
        assert str(conn.execute(text("SELECT birthday FROM holds WHERE id = :id"), {"id": hold_id}).scalar_one()) == day
    assert _draft(funnel, hold_id, None).status_code == 200
    with engine.connect() as conn:
        assert conn.execute(text("SELECT birthday FROM holds WHERE id = :id"), {"id": hold_id}).scalar_one() is None


def test_birthday_is_validated_server_side(db: Session) -> None:
    funnel, hold_id = _new_hold(db, "203.0.113.181")
    past = _draft(funnel, hold_id, (today() - timedelta(days=1)).isoformat())
    assert past.status_code == 422 and "already passed" in past.json()["detail"]
    far = _draft(funnel, hold_id, (today() + timedelta(days=400)).isoformat())
    assert far.status_code == 422 and "more than a year" in far.json()["detail"]
    assert _draft(funnel, hold_id, "not-a-date").status_code == 422
    assert _draft(funnel, hold_id, today().isoformat()).status_code == 200, "today is the earliest allowed"
    assert _draft(funnel, hold_id, (today() + timedelta(days=366)).isoformat()).status_code == 200


def test_birthday_flows_to_the_booking_and_confirmation(db: Session, engine: Engine) -> None:
    funnel, hold_id = _new_hold(db, "203.0.113.182")
    day = today() + timedelta(days=12)
    assert checkout(funnel, hold_id, 1, birthday=day.isoformat()).status_code == 200

    pending = client().get(f"/api/holds/{hold_id}").json()["party"]
    assert pending["birthday"] is None and pending["kid_name"] is None, "never exposed on a pending hold"

    assert deliver(payment_event(hold_id)).json() == {"outcome": "booked"}
    booked = client().get(f"/api/holds/{hold_id}").json()["party"]
    assert booked["kid_name"] == "Sam" and booked["kid_age"] == 8
    assert booked["birthday"] == day.isoformat()
    code = booked["confirmation_code"]
    by_code = client().get(f"/api/bookings/{code}").json()["party"]
    assert (by_code["kid_name"], by_code["kid_age"], by_code["birthday"]) == ("Sam", 8, day.isoformat())
    assert "@" not in client().get(f"/api/bookings/{code}").text, "still no contact details"


def test_booking_without_a_birthday_is_unchanged(db: Session) -> None:
    hold_id, ref = create_hold_via_api(db)
    deliver(payment_event(ref))
    party = client().get(f"/api/holds/{hold_id}").json()["party"]
    assert party["birthday"] is None


def test_owner_sees_the_day_of_line(db: Session) -> None:
    funnel, hold_id = _new_hold(db, "203.0.113.183")
    day = today() + timedelta(days=20)
    checkout(funnel, hold_id, 1, birthday=day.isoformat())
    deliver(payment_event(hold_id))
    (booking,) = admin_client().get("/api/admin/bookings").json()
    party_day = next_saturday(7)
    expected = f"Sam turns 8 {day:%b} {day.day}, party {party_day:%b} {party_day.day}"
    assert booking["birthday_line"] == expected
    assert booking["birthday"] == day.isoformat()


def test_owner_line_is_absent_without_a_birthday(db: Session) -> None:
    _, ref = create_hold_via_api(db)
    deliver(payment_event(ref))
    (booking,) = admin_client().get("/api/admin/bookings").json()
    assert booking["birthday_line"] is None


def test_checkout_rejects_a_past_birthday_and_changes_nothing(db: Session) -> None:
    funnel, hold_id = _new_hold(db, "203.0.113.184")
    response = checkout(funnel, hold_id, 1, birthday=(today() - timedelta(days=3)).isoformat())
    assert response.status_code == 422
    assert client().get(f"/api/holds/{hold_id}").json()["checkout_url"] is None


def test_request_carries_the_birthday_to_admin(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The request door is shut in v1, so this opens it: the path has to keep working for the day it reopens."""
    from tests.test_requests import next_weekday, request_body

    monkeypatch.setenv("REQUESTS_ENABLED", "true")
    get_settings.cache_clear()
    day = next_weekday(10)
    birthday = today() + timedelta(days=30)
    body = request_body(day, 8, birthday=birthday.isoformat(), kid_name="Mia", kid_age=9)
    assert client_from("203.0.113.185").post("/api/requests", json=body).status_code == 201
    (row,) = admin_client().get("/api/admin/requests").json()
    assert row["birthday_line"] == f"Mia turns 9 {birthday:%b} {birthday.day}, party {day:%b} {day.day}"
