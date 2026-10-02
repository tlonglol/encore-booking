"""Notes: free text for the host, never required, shown only where it is actually used.

Covers SPEC required test 26: the notes typed on Info + Deposit travel onto the booking, stay off a
pending hold's party details, reach the owner's NEW PARTY text and admin, show on the confirmation and
`GET /api/bookings/{code}`, and are editable from manage under the same cutoff as add-on additions.
"""

from datetime import timedelta

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.models import Booking
from tests.helpers import (
    admin_client,
    checkout,
    client,
    client_from,
    deliver,
    make_booking,
    make_hold,
    make_slot,
    next_saturday,
    payment_event,
    start_hold,
    today,
)


def _owner_body(engine: Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT body FROM notifications WHERE audience = 'owner' AND channel = 'sms'")
        ).scalar_one()


def _booking_on(db: Session, days_away: int = 30) -> Booking:
    slot = make_slot(db, today() + timedelta(days=days_away))
    return make_booking(db, make_hold(db, slot), slot)


def _add(booking: Booking, **overrides: object):  # type: ignore[no-untyped-def]
    body = {"addons": {}, "kids": booking.kids}
    body.update(overrides)
    return client().post(f"/api/manage/{booking.manage_token}/addons", json=body)


# ---- the funnel: typed at checkout, snapshotted onto the hold and the booking ----


def test_notes_travel_from_checkout_onto_the_booking(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.70")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    response = checkout(funnel, hold_id, 1, notes="  Theme is Minecraft, bring a Switch dock  ")
    assert response.status_code == 200, response.text

    with engine.connect() as conn:
        stored = conn.execute(text("SELECT notes FROM holds WHERE id = :id"), {"id": hold_id}).scalar_one()
    assert stored == "Theme is Minecraft, bring a Switch dock", "trimmed, and never required to be anything"

    deliver(payment_event(hold_id))
    with engine.connect() as conn:
        booked = conn.execute(text("SELECT notes FROM bookings WHERE hold_id = :id"), {"id": hold_id}).scalar_one()
    assert booked == "Theme is Minecraft, bring a Switch dock"


def test_empty_notes_changes_nothing_about_checkout(db: Session, engine: Engine) -> None:
    """Same package, same kids, same add-ons as the notes test above; only notes differs (absent here)."""
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.71")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    response = checkout(funnel, hold_id, 1)
    assert response.status_code == 200
    assert response.json()["party"]["total_cents"] == 54400, "identical to a checkout that did carry notes"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT notes FROM holds WHERE id = :id"), {"id": hold_id}).scalar_one() is None


def test_notes_are_never_on_a_pending_holds_party_details(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.73")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    checkout(funnel, hold_id, 1, notes="Secret theme, don't tell the birthday kid")
    pending = client().get(f"/api/holds/{hold_id}").json()
    assert pending["status"] == "pending_payment"
    assert pending["party"]["notes"] is None, "not shown before there is a real booking to attach it to"


# ---- where notes are actually used once the party is booked ----


def test_notes_reach_the_owner_sms(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.74")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    checkout(funnel, hold_id, 1, notes="Loves Mario Kart")
    deliver(payment_event(hold_id))
    assert "Notes: Loves Mario Kart" in _owner_body(engine)


def test_no_notes_means_no_notes_line_in_the_owner_sms(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.75")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    checkout(funnel, hold_id, 1)
    deliver(payment_event(hold_id))
    assert "Notes:" not in _owner_body(engine)


def test_notes_show_in_admins_booking_list(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.76")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    checkout(funnel, hold_id, 1, notes="Allergic kid at the party, ask the parent")
    deliver(payment_event(hold_id))
    (booking,) = admin_client().get("/api/admin/bookings").json()
    assert booking["notes"] == "Allergic kid at the party, ask the parent"


def test_notes_show_on_the_confirmation_page(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.77")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    checkout(funnel, hold_id, 1, notes="Bring extra controllers")
    deliver(payment_event(hold_id))
    code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]
    assert client().get(f"/api/bookings/{code}").json()["party"]["notes"] == "Bring extra controllers"


# ---- manage: editable under the same cutoff as add-on additions ----


def test_notes_are_editable_from_manage(db: Session, engine: Engine) -> None:
    booking = _booking_on(db)
    response = _add(booking, notes="Please set up early for a surprise entrance")
    assert response.status_code == 200, response.text
    db.refresh(booking)
    assert booking.notes == "Please set up early for a surprise entrance"


def test_a_notes_only_edit_succeeds_without_adding_anything(db: Session) -> None:
    """The guard that refuses an addons request which changed nothing must not also refuse this."""
    booking = _booking_on(db)
    response = _add(booking, addons={}, kids=booking.kids, notes="New notes, nothing else changed")
    assert response.status_code == 200, response.text


def test_truly_nothing_changed_is_still_refused(db: Session) -> None:
    booking = _booking_on(db)
    response = _add(booking, addons={"pizza_pack": 1}, kids=booking.kids)  # already has 1 pizza pack
    assert response.status_code == 409
    assert "Nothing was added" in response.json()["detail"]


def test_clearing_notes_to_empty_is_a_real_edit(db: Session) -> None:
    booking = _booking_on(db)
    _add(booking, notes="will be cleared")
    db.refresh(booking)
    assert booking.notes == "will be cleared"
    response = _add(booking, addons={"pizza_pack": 1}, kids=booking.kids, notes="")
    assert response.status_code == 200, response.text
    db.refresh(booking)
    assert booking.notes is None


def test_notes_edits_close_under_the_same_addon_cutoff(db: Session) -> None:
    """Default cutoff is 48 hours, same number that closes add-on additions."""
    booking = _booking_on(db, days_away=1)
    response = _add(booking, notes="too late to change this")
    assert response.status_code == 409
    assert "(646) 203-8087" in response.json()["detail"]
    db.refresh(booking)
    assert booking.notes is None


def test_the_owner_is_notified_of_a_notes_only_change(db: Session, engine: Engine) -> None:
    booking = _booking_on(db)
    _add(booking, notes="notified about this one")
    with engine.connect() as conn:
        body = conn.execute(
            text(
                "SELECT body FROM notifications WHERE audience = 'owner' AND channel = 'sms'"
                " ORDER BY id DESC LIMIT 1"
            )
        ).scalar_one()
    assert "notes updated" in body
