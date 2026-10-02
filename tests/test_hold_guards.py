"""Hold abuse guards.

Holds cost nothing to make and lock revenue while they live, so there are four of them: one date per
browser enforced on the server, a creation rate per IP, a per-date cooldown after a browser lets the same
date lapse twice, and a notice to the owner when someone is working through the calendar. All four are
deliberately gentle: a mobile network puts many parents behind one address.
"""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Hold
from tests.helpers import (
    browser_from,
    client,
    client_from,
    make_booking,
    make_hold,
    make_slot,
    next_saturday,
    start_hold,
)


@pytest.fixture
def reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _active(db: Session) -> list[Hold]:
    return (
        db.query(Hold)
        .filter(Hold.status == "pending_payment", Hold.expires_at > text("now()"))
        .order_by(Hold.created_at)
        .all()
    )


def _grid_status(day) -> str:
    body = client().get("/api/availability?weekends=52").json()
    return next(entry["status"] for entry in body["days"] if entry["date"] == day.isoformat())


# ---------- one browser, one date ----------


def test_a_second_confirm_swaps_the_hold_without_being_asked(db: Session) -> None:
    """The client sends no replace_hold_id at all here, which is the case that used to stack two holds."""
    first, second = make_slot(db, next_saturday(7)), make_slot(db, next_saturday(14))
    browser = browser_from("203.0.113.10")

    assert start_hold(browser, first.id).status_code == 201
    assert start_hold(browser, second.id).status_code == 201

    holds = _active(db)
    assert len(holds) == 1
    assert holds[0].slot_id == second.id
    assert _grid_status(first.local_date) == "open"
    assert _grid_status(second.local_date) == "held"


def test_a_second_tab_swaps_rather_than_stacking(db: Session) -> None:
    """Two tabs share a cookie jar the way two tabs of one browser share cookies."""
    first, second = make_slot(db, next_saturday(7)), make_slot(db, next_saturday(14))
    browser = browser_from("203.0.113.11")
    start_hold(browser, first.id)

    tab = client_from("203.0.113.11")
    tab.cookies.update(browser.cookies)
    assert start_hold(tab, second.id).status_code == 201

    assert len(_active(db)) == 1
    assert _grid_status(first.local_date) == "open"


def test_two_browsers_hold_two_dates(db: Session) -> None:
    """The guarantee is per browser, not per address: two parents on one network still get a date each."""
    first, second = make_slot(db, next_saturday(7)), make_slot(db, next_saturday(14))
    assert start_hold(browser_from("203.0.113.12"), first.id).status_code == 201
    assert start_hold(browser_from("203.0.113.12"), second.id).status_code == 201
    assert len(_active(db)) == 2


def test_confirming_the_same_date_twice_leaves_one_hold(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    browser = browser_from("203.0.113.13")
    assert start_hold(browser, slot.id).status_code == 201
    assert start_hold(browser, slot.id).status_code == 201
    assert len(_active(db)) == 1


def test_the_session_cookie_is_issued_once_and_kept(db: Session) -> None:
    browser = browser_from("203.0.113.14")
    first = browser.get("/api/availability")
    issued = first.cookies.get("encore_session")
    assert issued
    second = browser.get("/api/availability")
    assert second.cookies.get("encore_session") is None  # not reset on every request
    assert browser.cookies.get("encore_session") == issued


def test_a_tampered_session_cookie_is_replaced(db: Session) -> None:
    browser = client_from("203.0.113.15")
    browser.cookies.set("encore_session", "../../etc/passwd")
    response = browser.get("/api/availability")
    assert response.status_code == 200
    assert response.cookies.get("encore_session") not in (None, "../../etc/passwd")


# ---------- creation rate ----------


def test_the_creation_rate_is_per_ip_per_hour(db: Session, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("HOLD_RATE_PER_HOUR", "3")
    get_settings.cache_clear()
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 6)]
    for slot in slots[:3]:
        assert start_hold(browser_from("203.0.113.20"), slot.id).status_code == 201
    blocked = start_hold(browser_from("203.0.113.20"), slots[3].id)
    assert blocked.status_code == 429
    assert "(646) 203-8087" in blocked.json()["detail"]


def test_swapping_dates_does_not_burn_the_rate(db: Session, monkeypatch, reset_settings) -> None:
    """A parent changing their mind locks nothing: the swap releases the old date in the same transaction.
    Counting those would lock out the most normal behaviour in the funnel."""
    monkeypatch.setenv("HOLD_RATE_PER_HOUR", "3")
    get_settings.cache_clear()
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 7)]
    browser = browser_from("203.0.113.21")
    for slot in slots:
        assert start_hold(browser, slot.id).status_code == 201, slot.local_date
    assert len(_active(db)) == 1


def test_an_hour_old_hold_does_not_count(db: Session, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("HOLD_RATE_PER_HOUR", "1")
    get_settings.cache_clear()
    old_slot, new_slot = make_slot(db, next_saturday(7)), make_slot(db, next_saturday(14))
    assert start_hold(browser_from("203.0.113.22"), old_slot.id).status_code == 201
    db.execute(text("UPDATE holds SET created_at = now() - interval '2 hours'"))
    db.commit()
    assert start_hold(browser_from("203.0.113.22"), new_slot.id).status_code == 201


# ---------- per-date cooldown ----------


def _lapse(db: Session, browser, slot) -> None:  # type: ignore[no-untyped-def]
    assert start_hold(browser, slot.id).status_code == 201
    db.execute(
        text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE slot_id = :id"), {"id": slot.id}
    )
    db.commit()


def test_one_expiry_then_a_re_hold_still_works(db: Session) -> None:
    """The expiry modal's whole purpose. The cooldown must not touch it."""
    slot = make_slot(db, next_saturday(7))
    browser = browser_from("203.0.113.30")
    _lapse(db, browser, slot)
    assert start_hold(browser, slot.id).status_code == 201


def test_two_lapsed_holds_on_one_date_start_a_cooldown(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    browser = browser_from("203.0.113.31")
    _lapse(db, browser, slot)
    _lapse(db, browser, slot)

    blocked = start_hold(browser, slot.id)
    assert blocked.status_code == 429
    assert "let this date go twice" in blocked.json()["detail"]
    assert blocked.json()["expires_at"]


def test_the_cooled_down_date_is_still_open_to_everyone_else(db: Session) -> None:
    """The guard is about one browser, not the date. Nobody else is punished for it."""
    slot = make_slot(db, next_saturday(7))
    browser = browser_from("203.0.113.32")
    _lapse(db, browser, slot)
    _lapse(db, browser, slot)
    assert _grid_status(slot.local_date) == "open"
    assert start_hold(browser_from("203.0.113.99"), slot.id).status_code == 201


def test_the_cooldown_is_per_date(db: Session) -> None:
    first, second = make_slot(db, next_saturday(7)), make_slot(db, next_saturday(14))
    browser = browser_from("203.0.113.33")
    _lapse(db, browser, first)
    _lapse(db, browser, first)
    assert start_hold(browser, first.id).status_code == 429
    assert start_hold(browser, second.id).status_code == 201


def test_the_cooldown_lifts(db: Session, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("HOLD_COOLDOWN_MINUTES", "45")
    get_settings.cache_clear()
    slot = make_slot(db, next_saturday(7))
    browser = browser_from("203.0.113.34")
    _lapse(db, browser, slot)
    _lapse(db, browser, slot)
    assert start_hold(browser, slot.id).status_code == 429

    db.execute(text("UPDATE holds SET expires_at = now() - interval '46 minutes' WHERE slot_id = :id"), {"id": slot.id})
    db.commit()
    assert start_hold(browser, slot.id).status_code == 201


def test_a_hold_that_became_a_booking_is_not_one_of_the_two(db: Session) -> None:
    """A parent who booked this date before, cancelled, and has since let one hold lapse has lapsed once,
    not twice. Only holds that went nowhere count."""
    slot = make_slot(db, next_saturday(7))
    browser = browser_from("203.0.113.35")
    session_id = browser.get("/api/availability").cookies.get("encore_session")

    # A party this browser booked on this date and later cancelled, so the date is free again.
    booked = make_hold(db, slot, status="booked", expires_in=timedelta(minutes=-60))
    make_booking(db, booked, slot)
    db.execute(
        text("UPDATE holds SET session_id = :session_id WHERE id = :id"),
        {"session_id": session_id, "id": booked.id},
    )
    db.execute(text("UPDATE bookings SET cancelled_at = now() WHERE hold_id = :id"), {"id": booked.id})
    db.execute(text("UPDATE holds SET status = 'cancelled' WHERE id = :id"), {"id": booked.id})
    db.commit()

    # One lapse since. One is not two, so the date is still theirs to take.
    _lapse(db, browser, slot)
    assert start_hold(browser, slot.id).status_code == 201


# ---------- spike notice ----------


def test_a_visitor_working_through_the_calendar_tells_the_owner(db: Session) -> None:
    """One browser walking the grid, each date swapping the last. Nothing is blocked, because swapping
    locks nothing, and nothing should be: this is the case the caps deliberately do not catch."""
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 13)]
    browser = browser_from("203.0.113.40")
    for slot in slots:
        assert start_hold(browser, slot.id).status_code == 201

    bodies = [row.body for row in db.execute(text("SELECT body FROM notifications WHERE event = 'hold_spike'"))]
    assert len(bodies) == 1
    assert "HEADS UP" in bodies[0]
    assert "Nothing is blocked" in bodies[0]
    # One date held at the end of it, which is why none of the caps fired.
    assert len(_active(db)) == 1


def test_the_owner_is_told_once_an_hour_not_once_a_hold(db: Session) -> None:
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 21)]
    browser = browser_from("203.0.113.41")
    for slot in slots:
        assert start_hold(browser, slot.id).status_code == 201
    assert db.execute(text("SELECT count(*) FROM notifications WHERE event = 'hold_spike'")).scalar_one() == 1


def test_a_quiet_day_tells_the_owner_nothing(db: Session) -> None:
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 4)]
    for slot in slots:
        start_hold(browser_from("203.0.113.42"), slot.id)
    assert db.execute(text("SELECT count(*) FROM notifications WHERE event = 'hold_spike'")).scalar_one() == 0


# ---------- Turnstile ----------


def test_turnstile_is_off_and_the_funnel_runs_without_a_token(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    assert get_settings().turnstile_enabled is False
    assert start_hold(browser_from("203.0.113.50"), slot.id).status_code == 201


def test_turnstile_on_refuses_a_hold_with_no_token(db: Session, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    slot = make_slot(db, next_saturday(7))
    response = start_hold(browser_from("203.0.113.51"), slot.id)
    assert response.status_code == 403
    assert db.query(Hold).count() == 0
