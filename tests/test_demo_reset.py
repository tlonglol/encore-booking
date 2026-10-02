"""The demo reset: what it lays down, and the three things that stop it running by accident."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Booking, Hold
from tests.helpers import admin_client, client, make_booking, make_hold, make_slot, today


@pytest.fixture(autouse=True)
def reset_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEMO_RESET_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _grid() -> dict[str, str]:
    body = client().get("/api/availability").json()
    return {entry["date"]: entry["status"] for entry in body["days"]}


def test_reset_is_refused_without_the_admin_password(db: Session) -> None:
    assert client().post("/api/admin/demo/reset").status_code == 401


def test_reset_is_refused_while_the_flag_is_off(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_RESET_ENABLED", "false")
    get_settings.cache_clear()
    response = admin_client().post("/api/admin/demo/reset")
    assert response.status_code == 403
    assert "DEMO_RESET_ENABLED" in response.json()["detail"]


def test_reset_refuses_against_production_credentials(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """A password is one leak away from someone wiping a real calendar."""
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "production")
    get_settings.cache_clear()
    response = admin_client().post("/api/admin/demo/reset")
    assert response.status_code == 403
    assert "production" in response.json()["detail"]


def test_reset_leaves_a_grid_with_open_booked_and_one_hold(db: Session) -> None:
    response = admin_client().post("/api/admin/demo/reset")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bookings"] == 3
    assert len(body["held"]) == 1
    assert body["open"] > 0

    grid = _grid()
    assert all(grid[day] == "booked" for day in body["booked"])
    assert all(grid[day] == "held" for day in body["held"])
    assert "open" in grid.values()


def test_the_sample_bookings_show_up_in_admin(db: Session) -> None:
    admin_client().post("/api/admin/demo/reset")
    rows = admin_client().get("/api/admin/bookings").json()
    assert len(rows) == 3
    assert all(row["name"] and row["phone"] and row["email"] for row in rows)
    assert all(row["balance_cents"] == row["total_cents"] - row["deposit_cents"] for row in rows)
    # The owner's day-of line needs a birthday, so the samples carry one.
    assert all(row["birthday_line"] for row in rows)


def test_the_sample_hold_shows_up_in_admin(db: Session) -> None:
    admin_client().post("/api/admin/demo/reset")
    rows = admin_client().get("/api/admin/holds").json()
    assert len(rows) == 1
    assert rows[0]["seconds_remaining"] > 0
    assert rows[0]["package"] == "Plus"


def test_sample_bookings_are_priced_by_the_pricing_function(db: Session) -> None:
    from app.pricing import quote

    admin_client().post("/api/admin/demo/reset")
    for booking in db.query(Booking).all():
        expected = quote(booking.package, booking.kids, booking.addons)
        assert booking.total_cents == expected.total_cents
        assert booking.deposit_cents == 10000


def test_reset_clears_whatever_the_last_run_left_behind(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=30))
    stale = make_booking(db, make_hold(db, slot), slot)
    client().post(f"/api/manage/{stale.manage_token}/cancel", json={})
    db.execute(
        text("INSERT INTO waitlist (slot_id, email) VALUES (:slot, 'old@example.com')"), {"slot": slot.id}
    )
    db.commit()

    admin_client().post("/api/admin/demo/reset")
    # Identified by its confirmation code, not its id: the truncate restarts the sequence, so a fresh
    # sample booking takes the old row's id.
    assert db.query(Booking).filter(Booking.confirmation_code == stale.confirmation_code).count() == 0
    assert db.execute(text("SELECT count(*) FROM refunds")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM notifications")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM waitlist")).scalar_one() == 0
    assert db.query(Booking).count() == 3


def test_reset_unblocks_any_date_the_last_demo_blocked(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=30))
    assert admin_client().post(f"/api/admin/slots/{slot.id}/block").status_code == 200
    admin_client().post("/api/admin/demo/reset")
    assert db.execute(text("SELECT count(*) FROM slots WHERE blocked_at IS NOT NULL")).scalar_one() == 0


def test_reset_is_repeatable(db: Session) -> None:
    """Run twice between demos, it has to land in the same place both times."""
    first = admin_client().post("/api/admin/demo/reset").json()
    second = admin_client().post("/api/admin/demo/reset").json()
    assert first["booked"] == second["booked"]
    assert first["held"] == second["held"]
    assert db.query(Booking).count() == 3
    assert db.query(Hold).filter(Hold.status == "pending_payment").count() == 1


def test_the_demo_state_is_bookable_afterwards(db: Session) -> None:
    """Whatever it lays down has to leave a date a parent can actually take, or the demo stops at step one."""
    admin_client().post("/api/admin/demo/reset")
    open_day = next(day for day, status in _grid().items() if status == "open")
    slot_id = next(
        entry["slot_id"] for entry in client().get("/api/availability").json()["days"] if entry["date"] == open_day
    )
    assert client().post("/api/holds", json={"slot_id": slot_id}).status_code == 201


def test_reset_never_offers_a_date_inside_the_lead_time_window(db: Session) -> None:
    body = admin_client().post("/api/admin/demo/reset").json()
    first_bookable = client().get("/api/availability").json()["first_bookable_date"]
    assert all(day >= first_bookable for day in body["booked"] + body["held"])
