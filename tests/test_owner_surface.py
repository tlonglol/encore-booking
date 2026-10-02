"""The owner's view: the bookings list, the live holds beside it, and a notice for every money event."""

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import FakeSquareApi
from tests.helpers import (
    admin_client,
    client,
    client_from,
    create_hold_via_api,
    deliver,
    make_booking,
    make_hold,
    make_slot,
    payment_event,
    start_hold,
    today,
)
from tests.test_refund_webhook import refund_event


def test_everything_in_the_owner_surface_needs_the_password(db: Session) -> None:
    for path in ("/api/admin/bookings", "/api/admin/holds", "/api/admin/refunds", "/api/admin/repeat-cancellers"):
        assert client().get(path).status_code == 401, path


def test_the_bookings_list_is_newest_first(db: Session) -> None:
    for offset in (30, 37, 44):
        slot = make_slot(db, today() + timedelta(days=offset))
        make_booking(db, make_hold(db, slot), slot)
    rows = admin_client().get("/api/admin/bookings").json()
    booked_at = [row["booked_at"] for row in rows]
    assert booked_at == sorted(booked_at, reverse=True)


def test_a_booking_row_carries_what_the_owner_needs(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=30))
    make_booking(db, make_hold(db, slot), slot)
    row = admin_client().get("/api/admin/bookings").json()[0]
    for field in ("day_label", "time_label", "name", "phone", "email", "package", "kids", "addons",
                  "paid_amount_cents", "balance_cents", "deposit_status", "cancelled_at"):
        assert field in row, field
    assert row["balance_cents"] == row["total_cents"] - row["deposit_cents"]


def test_a_booking_row_shows_moves_and_refunds(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=30))
    booking = make_booking(db, make_hold(db, slot), slot)
    target = make_slot(db, today() + timedelta(days=45))
    client().post(f"/api/manage/{booking.manage_token}/reschedule", json={"slot_id": target.id})
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})

    row = admin_client().get("/api/admin/bookings").json()[0]
    assert row["reschedule_count"] == 1
    assert row["rescheduled_at"] is not None
    assert row["cancel_source"] == "parent"
    assert row["refund_status"] == "pending"


def test_active_holds_are_listed_with_their_created_at(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=14))
    created = start_hold(client_from("203.0.113.40"), slot.id)
    assert created.status_code == 201

    rows = admin_client().get("/api/admin/holds").json()
    assert len(rows) == 1
    assert rows[0]["hold_id"] == created.json()["hold_id"]
    assert rows[0]["created_at"]
    assert 0 < rows[0]["seconds_remaining"] <= 15 * 60
    # Nothing has been typed yet, so the owner sees an anonymous hold rather than a blank name.
    assert rows[0]["name"] is None


def test_a_hold_that_reached_checkout_shows_its_contact(db: Session) -> None:
    create_hold_via_api(db, days_ahead=7, n=3)
    row = admin_client().get("/api/admin/holds").json()[0]
    assert row["name"] == "Pat Parent3"
    assert row["reached_checkout"] is True
    assert row["package"] == "Standard"


def test_lapsed_holds_are_not_listed(db: Session) -> None:
    """Expiry is lazy everywhere, so this list asks the same question the availability grid does."""
    slot = make_slot(db, today() + timedelta(days=14))
    make_hold(db, slot, expires_in=timedelta(minutes=-1))
    assert admin_client().get("/api/admin/holds").json() == []


def test_a_booked_hold_is_not_listed(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=14))
    make_booking(db, make_hold(db, slot, status="booked"), slot)
    assert admin_client().get("/api/admin/holds").json() == []


def test_holds_are_newest_first(db: Session) -> None:
    for n, offset in enumerate((14, 21, 28)):
        slot = make_slot(db, today() + timedelta(days=offset))
        assert start_hold(client_from(f"203.0.113.{60 + n}"), slot.id).status_code == 201
    created = [row["created_at"] for row in admin_client().get("/api/admin/holds").json()]
    assert created == sorted(created, reverse=True)


def _owner_bodies(db: Session, event: str) -> list[str]:
    return [
        row.body
        for row in db.execute(
            text("SELECT body FROM notifications WHERE audience = 'owner' AND event = :event"), {"event": event}
        )
    ]


def test_the_owner_hears_about_a_booking(db: Session) -> None:
    _, ref = create_hold_via_api(db, days_ahead=14, n=4)
    deliver(payment_event(ref))
    bodies = _owner_bodies(db, "booked")
    assert len(bodies) == 1
    assert bodies[0].startswith("NEW PARTY:")


def test_the_owner_hears_about_a_cancellation(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=30))
    booking = make_booking(db, make_hold(db, slot), slot)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    assert len(_owner_bodies(db, "cancelled")) == 1


def test_the_owner_hears_when_the_refund_actually_lands(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=30))
    booking = make_booking(db, make_hold(db, slot), slot)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    refund_id = db.execute(text("SELECT square_refund_id FROM refunds")).scalar_one()

    deliver(refund_event(refund_id, status="COMPLETED"))
    bodies = _owner_bodies(db, "refunded")
    assert len(bodies) == 1
    assert "REFUND CONFIRMED" in bodies[0]


def test_a_failed_refund_tells_the_owner_to_finish_it_by_hand(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=30))
    booking = make_booking(db, make_hold(db, slot), slot)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    refund_id = db.execute(text("SELECT square_refund_id FROM refunds")).scalar_one()

    deliver(refund_event(refund_id, status="FAILED"))
    bodies = _owner_bodies(db, "refunded")
    assert len(bodies) == 1
    assert "Finish it in Square" in bodies[0]


def test_the_pending_hop_is_not_announced(db: Session) -> None:
    """Square answers PENDING first and COMPLETED later. Only the ending is news."""
    slot = make_slot(db, today() + timedelta(days=30))
    booking = make_booking(db, make_hold(db, slot), slot)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    refund_id = db.execute(text("SELECT square_refund_id FROM refunds")).scalar_one()

    deliver(refund_event(refund_id, status="PENDING", event_type="refund.created"))
    assert _owner_bodies(db, "refunded") == []


def test_a_redelivered_refund_event_announces_once(db: Session) -> None:
    slot = make_slot(db, today() + timedelta(days=30))
    booking = make_booking(db, make_hold(db, slot), slot)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    refund_id = db.execute(text("SELECT square_refund_id FROM refunds")).scalar_one()

    raw = refund_event(refund_id, status="COMPLETED")
    deliver(raw)
    deliver(raw)
    deliver(refund_event(refund_id, status="COMPLETED"))
    assert len(_owner_bodies(db, "refunded")) == 1


def test_messages_are_logged_when_twilio_is_absent(db: Session) -> None:
    """The test environment has no Twilio credentials, which is the owner-demo fallback: log the body."""
    slot = make_slot(db, today() + timedelta(days=30))
    booking = make_booking(db, make_hold(db, slot), slot)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    row = db.execute(
        text("SELECT status, recipient, body FROM notifications WHERE event = 'cancelled'")
    ).one()
    assert row.status == "logged"
    assert row.body


def test_a_cancellation_notice_never_fails_the_cancellation(db: Session, messaging) -> None:
    """Notifications are best-effort: the booking is already cancelled and the date already freed."""
    messaging.raise_error = RuntimeError("network down")
    slot = make_slot(db, today() + timedelta(days=30))
    booking = make_booking(db, make_hold(db, slot), slot)
    assert client().post(f"/api/manage/{booking.manage_token}/cancel", json={}).status_code == 200
    db.refresh(booking)
    assert booking.cancelled_at is not None
