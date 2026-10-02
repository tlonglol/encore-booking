import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.models import Booking, Slot
from app.timeutil import slot_start_utc
from tests.conftest import FakeSquareApi
from tests.helpers import (
    admin_client,
    client,
    client_from,
    create_hold_via_api,
    deliver,
    make_slot,
    next_saturday,
    next_tuesday,
    payment_event,
    start_hold,
    today,
)


def _book(db: Session, days_ahead: int = 7, n: int = 1) -> tuple[str, str]:
    hold_id, ref = create_hold_via_api(db, days_ahead, n)
    deliver(payment_event(ref, payment_id=f"pay-{hold_id}"))
    code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]
    return hold_id, code


def manual_body(day: date, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "date": day.isoformat(),
        "package": "standard",
        "kids": 18,
        "addons": {"pizza_pack": 1},
        "first_name": "Dana",
        "last_name": "Caller",
        "phone": "(914) 555-7788",
        "email": "Dana.Caller@Example.com",
        "kid_name": "Robin",
        "kid_age": 9,
        "notes": "Called the shop, wants the back room",
        "deposit_collected": True,
    }
    body.update(overrides)
    return body


def test_admin_endpoints_require_login() -> None:
    anonymous = client()
    for path in ("/api/admin/bookings", "/api/admin/flags", "/api/admin/notifications", "/api/admin/session"):
        assert anonymous.get(path).status_code == 401
    assert anonymous.post("/api/admin/bookings/1/cancel", json={}).status_code == 401
    assert anonymous.post("/api/admin/flags/1/resolve").status_code == 401
    assert anonymous.post("/api/admin/bookings", json=manual_body(next_saturday(9))).status_code == 401
    assert anonymous.post("/api/admin/bookings/1/balance-paid", json={"paid": True}).status_code == 401


def test_bookings_list_shows_what_the_owner_needs(db: Session) -> None:
    _book(db)
    (booking,) = admin_client().get("/api/admin/bookings").json()
    assert booking["package"] == "Standard"
    assert booking["kids"] == 18
    assert booking["addons"] == "Pizza Pack x1"
    assert booking["total_cents"] == 54400
    assert booking["balance_cents"] == 44400
    assert booking["deposit_status"] == "paid"
    assert booking["phone"] == "+19145550001"
    assert booking["kid_name"] == "Sam"
    assert booking["kid_age"] == 8
    assert booking["notes"] is None, "the funnel form has no notes field"
    assert booking["time_label"] == "10:00 AM"
    assert booking["full_refund"] is True


def test_cancel_frees_the_slot_and_records_refund_data(db: Session, engine: Engine) -> None:
    hold_id, code = _book(db)
    admin = admin_client()
    (booking,) = admin.get("/api/admin/bookings").json()
    response = admin.post(f"/api/admin/bookings/{booking['id']}/cancel", json={"reason": "Parent called"})
    assert response.status_code == 200
    assert response.json()["full_refund"] is True
    assert admin.post(f"/api/admin/bookings/{booking['id']}/cancel", json={}).status_code == 409
    assert admin.post("/api/admin/bookings/999999/cancel", json={}).status_code == 404

    assert client().get(f"/api/bookings/{code}").json()["status"] == "cancelled"
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT b.created_at, b.event_starts_at, b.cancelled_at, h.status, h.slot_id FROM bookings b"
                 " JOIN holds h ON h.id = b.hold_id")
        ).one()
    assert row.cancelled_at is not None and row.status == "cancelled"
    retry = start_hold(client_from("203.0.113.200"), row.slot_id)
    assert retry.status_code == 201, "the cancelled date is bookable again"


def test_flags_are_visible_and_resolvable(db: Session, engine: Engine) -> None:
    hold_id, ref = create_hold_via_api(db)
    with engine.begin() as conn:
        conn.execute(text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": hold_id})
    deliver(payment_event(ref, payment_id="pay-late"))
    admin = admin_client()
    (flag,) = admin.get("/api/admin/flags").json()
    assert flag["kind"] == "paid_after_expiry"
    assert flag["phone"] == "+19145550001"
    assert flag["resolved_at"] is None
    assert admin.post(f"/api/admin/flags/{flag['id']}/resolve").json() == {"ok": True}
    assert admin.get("/api/admin/flags").json()[0]["resolved_at"] is not None
    assert admin.post("/api/admin/flags/999999/resolve").status_code == 404


def test_notification_log_is_visible(db: Session) -> None:
    _book(db)
    rows = admin_client().get("/api/admin/notifications").json()
    assert {(row["audience"], row["status"]) for row in rows} == {("parent", "logged"), ("owner", "logged")}


# ---------- manual booking entry ----------


def test_manual_booking_is_priced_by_the_server(db: Session, square_api: FakeSquareApi) -> None:
    slot = make_slot(db, next_saturday(9))
    admin = admin_client()
    created = admin.post("/api/admin/bookings", json=manual_body(slot.local_date, total_cents=1, deposit_cents=1))
    assert created.status_code == 201, created.text
    # Standard $399 + 3 kids over the included 15 + one Pizza Pack, the same quote the funnel produces.
    assert created.json()["total_cents"] == 54400
    assert created.json()["balance_cents"] == 44400

    (booking,) = admin.get("/api/admin/bookings").json()
    assert booking["origin"] == "manual"
    assert booking["total_cents"] == 54400, "the price the client sent was ignored"
    assert booking["paid_amount_cents"] == 10000
    assert booking["balance_cents"] == 44400
    assert booking["deposit_status"] == "collected in person"
    assert booking["phone"] == "+19145557788", "normalized to E.164 like any other booking"
    assert booking["email"] == "dana.caller@example.com"
    assert booking["kid_name"] == "Robin"
    assert booking["notes"] == "Called the shop, wants the back room"
    assert booking["balance_paid_at"] is None
    assert square_api.requests == [], "no card, so Square is never called"


def test_manual_booking_can_waive_the_deposit(db: Session) -> None:
    slot = make_slot(db, next_saturday(9))
    admin = admin_client()
    assert admin.post("/api/admin/bookings", json=manual_body(slot.local_date, deposit_collected=False)).status_code == 201
    (booking,) = admin.get("/api/admin/bookings").json()
    assert booking["paid_amount_cents"] == 0
    assert booking["deposit_status"] == "waived"
    assert booking["balance_cents"] == booking["total_cents"], "nothing was paid, so the whole total is owed"


def test_manual_booking_claims_the_date(db: Session) -> None:
    slot = make_slot(db, next_saturday(9))
    assert admin_client().post("/api/admin/bookings", json=manual_body(slot.local_date)).status_code == 201
    taken = start_hold(client_from("203.0.113.77"), slot.id)
    assert taken.status_code == 409, "the date is gone for parents too"
    days = {day["slot_id"]: day["status"] for day in client().get("/api/availability").json()["days"]}
    assert days[slot.id] == "booked"


def test_manual_booking_refuses_a_date_that_is_not_free(db: Session) -> None:
    admin = admin_client()

    held = make_slot(db, next_saturday(9))
    assert start_hold(client_from("203.0.113.78"), held.id).status_code == 201
    assert admin.post("/api/admin/bookings", json=manual_body(held.local_date)).status_code == 409

    blocked = make_slot(db, next_saturday(16))
    assert admin.post(f"/api/admin/slots/{blocked.id}/block").status_code == 200
    assert admin.post("/api/admin/bookings", json=manual_body(blocked.local_date)).status_code == 409

    booked = make_slot(db, next_saturday(23))
    assert admin.post("/api/admin/bookings", json=manual_body(booked.local_date)).status_code == 201
    assert admin.post("/api/admin/bookings", json=manual_body(booked.local_date)).status_code == 409

    assert db.scalar(select(func.count()).select_from(Booking)) == 1, "only the one that was allowed through"


def test_manual_booking_refuses_a_date_that_has_already_passed(db: Session) -> None:
    """The one date rule the owner does have. Lead time is not enforced here; yesterday is."""
    admin = admin_client()
    yesterday = today() - timedelta(days=1)
    refused = admin.post("/api/admin/bookings", json=manual_body(yesterday))
    assert refused.status_code == 422, refused.text
    assert "passed" in refused.json()["detail"]
    # Today is fine: a party booked by phone for this afternoon is exactly what this is for.
    assert admin.post("/api/admin/bookings", json=manual_body(today())).status_code == 201
    assert db.scalar(select(func.count()).select_from(Slot).where(Slot.local_date == yesterday)) == 0


def test_manual_booking_creates_the_slot_for_a_date_that_has_none(db: Session) -> None:
    """A date the parents' grid has never materialized. The slot row is written in the same transaction."""
    day = next_saturday(400)
    assert db.scalar(select(func.count()).select_from(Slot).where(Slot.local_date == day)) == 0
    created = admin_client().post("/api/admin/bookings", json=manual_body(day))
    assert created.status_code == 201, created.text

    slot = db.scalars(select(Slot).where(Slot.local_date == day)).one()
    assert slot.starts_at == slot_start_utc(day), "10:00 AM, the same default seeding uses"
    assert slot.blocked_at is None
    booking = db.scalars(select(Booking).where(Booking.slot_id == slot.id)).one()
    assert booking.event_starts_at == slot.starts_at


def test_two_submits_for_one_brand_new_date_make_one_slot_and_one_booking(db: Session) -> None:
    """The get-or-create race. The loser's insert blocks on the winner's uncommitted index entry, then
    finds the winner's slot and its booking, so it loses the way every other contested date loses."""
    day = next_saturday(400)
    barrier = threading.Barrier(2)

    def attempt(n: int) -> httpx.Response:
        admin = admin_client()
        barrier.wait()
        return admin.post("/api/admin/bookings", json=manual_body(day, email=f"new{n}@example.com"))

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(attempt, range(2)))

    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    assert db.scalar(select(func.count()).select_from(Slot).where(Slot.local_date == day)) == 1
    assert db.scalar(select(func.count()).select_from(Booking)) == 1


def test_a_refused_submit_leaves_no_slot_behind(db: Session) -> None:
    """The slot is created inside the booking's transaction, so a booking that is refused un-creates it."""
    day = next_saturday(400)
    admin = admin_client()
    assert admin.post("/api/admin/bookings", json=manual_body(day, kids=99)).status_code == 422
    assert db.scalar(select(func.count()).select_from(Slot)) == 0, "a mistyped party left nothing behind"


def test_a_weekday_the_owner_books_never_reaches_the_parents_grid(db: Session) -> None:
    """The owner can book a Tuesday. To every parent-facing surface that date still has no slot."""
    tuesday = next_tuesday(9)
    assert admin_client().post("/api/admin/bookings", json=manual_body(tuesday)).status_code == 201
    slot = db.scalars(select(Slot).where(Slot.local_date == tuesday)).one()

    days = {day["date"]: day for day in client().get("/api/availability").json()["days"]}
    assert days[tuesday.isoformat()]["status"] == "request", "a weekday is a request date, booked or not"
    assert days[tuesday.isoformat()]["slot_id"] is None, "and the grid is never handed its id"
    # Naming the id directly is the other way in, and it is the one DECISIONS 42 was worried about.
    assert start_hold(client_from("203.0.113.90"), slot.id).status_code == 404


def test_a_party_cannot_be_moved_onto_a_weekday_the_owner_created(db: Session) -> None:
    hold_id, _ = _book(db)
    tuesday = next_tuesday(16)
    assert admin_client().post("/api/admin/bookings", json=manual_body(tuesday)).status_code == 201
    weekday_slot = db.scalars(select(Slot).where(Slot.local_date == tuesday)).one()
    # Free it, so the only thing refusing the move is the date not being a weekend.
    (manual_booking,) = db.scalars(select(Booking).where(Booking.slot_id == weekday_slot.id)).all()
    token = db.scalar(select(Booking.manage_token).where(Booking.hold_id == uuid.UUID(hold_id)))
    assert client().post(f"/api/admin/bookings/{manual_booking.id}/cancel", json={}).status_code == 401
    admin_client().post(f"/api/admin/bookings/{manual_booking.id}/cancel", json={})

    moved = client().post(f"/api/manage/{token}/reschedule", json={"slot_id": weekday_slot.id})
    assert moved.status_code == 422, moved.text
    assert db.scalar(select(Booking.slot_id).where(Booking.manage_token == token)) != weekday_slot.id


# ---------- the date the owner is typing ----------


def test_slot_status_reports_a_free_date_and_creates_nothing(db: Session) -> None:
    admin = admin_client()
    day = next_saturday(400)
    answer = admin.get("/api/admin/slots/status", params={"date": day.isoformat()}).json()
    assert answer["status"] == "none", "no slot row yet, and the owner can still book it"
    assert answer["bookable"] is True
    assert answer["time_label"] == "10:00 AM"
    assert answer["weekday"] is False and answer["weekday_label"] is None
    assert answer["confirmation_code"] is None
    assert db.scalar(select(func.count()).select_from(Slot)) == 0, "asking is not booking"

    make_slot(db, day)
    assert admin.get("/api/admin/slots/status", params={"date": day.isoformat()}).json()["status"] == "open"


def test_slot_status_names_what_is_sitting_on_a_date(db: Session) -> None:
    admin = admin_client()

    def status(day: date) -> dict[str, object]:
        return admin.get("/api/admin/slots/status", params={"date": day.isoformat()}).json()

    booked = next_saturday(9)
    created = admin.post("/api/admin/bookings", json=manual_body(booked))
    assert created.status_code == 201, created.text
    answer = status(booked)
    assert answer["status"] == "booked" and answer["bookable"] is False
    assert answer["confirmation_code"] == created.json()["confirmation_code"]

    held = make_slot(db, next_saturday(16))
    assert start_hold(client_from("203.0.113.91"), held.id).status_code == 201
    assert status(held.local_date)["status"] == "held"

    blocked = make_slot(db, next_saturday(23))
    assert admin.post(f"/api/admin/slots/{blocked.id}/block").status_code == 200
    assert status(blocked.local_date)["status"] == "blocked"

    past = status(today() - timedelta(days=1))
    assert past["status"] == "past" and past["bookable"] is False

    weekday = status(next_tuesday(9))
    assert weekday["weekday"] is True, "bookable, and the form says what day of the week it is"
    assert weekday["bookable"] is True
    assert weekday["weekday_label"] == "Tuesday"


def test_manual_booking_loses_a_race_without_double_booking(db: Session) -> None:
    """The date goes between the form loading and the submit. The unique index decides, not the pre-check."""
    slot = make_slot(db, next_saturday(9))
    barrier = threading.Barrier(2)

    def attempt(n: int) -> httpx.Response:
        admin = admin_client()
        barrier.wait()
        return admin.post("/api/admin/bookings", json=manual_body(slot.local_date, email=f"race{n}@example.com"))

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(attempt, range(2)))

    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    live = db.scalar(
        select(func.count()).select_from(Booking).where(Booking.slot_id == slot.id, Booking.cancelled_at.is_(None))
    )
    assert live == 1


def test_manual_booking_tells_the_owner_and_not_the_parent(db: Session) -> None:
    slot = make_slot(db, next_saturday(9))
    admin = admin_client()
    assert admin.post("/api/admin/bookings", json=manual_body(slot.local_date)).status_code == 201
    notes = admin.get("/api/admin/notifications").json()
    assert {note["audience"] for note in notes} == {"owner"}, "the parent paid nothing here and is told nothing"
    (owner,) = notes
    assert owner["body"].startswith("ADDED BY HAND:")
    assert "collected in person" in owner["body"]
    assert "Notes: Called the shop" in owner["body"]


def test_cancelling_a_manual_booking_never_calls_square(db: Session, square_api: FakeSquareApi) -> None:
    """Its payment id is synthetic. Refunding it would be a guaranteed failure recorded as a pending refund."""
    slot = make_slot(db, next_saturday(30))
    admin = admin_client()
    assert admin.post("/api/admin/bookings", json=manual_body(slot.local_date)).status_code == 201
    (booking,) = admin.get("/api/admin/bookings").json()

    token = db.scalar(select(Booking.manage_token).where(Booking.id == booking["id"]))
    cancelled = client().post(f"/api/manage/{token}/cancel", json={"forfeit_deposit": True})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["refunded"] is False
    assert square_api.requests == [], "the owner hands the cash back; the system does not pretend to"
    assert db.scalar(select(func.count()).select_from(Booking).where(Booking.cancelled_at.is_not(None))) == 1
    days = {day["slot_id"]: day["status"] for day in client().get("/api/availability").json()["days"]}
    assert days[slot.id] == "open", "the date is free again"


def test_manual_booking_validates_what_it_is_given(db: Session) -> None:
    slot = make_slot(db, next_saturday(9))
    admin = admin_client()
    assert admin.post("/api/admin/bookings", json=manual_body(slot.local_date, phone="555")).status_code == 422
    assert admin.post("/api/admin/bookings", json=manual_body(slot.local_date, package="deluxe")).status_code == 422
    assert admin.post("/api/admin/bookings", json=manual_body(slot.local_date, kids=99)).status_code == 422
    # Highlight Reel without Tournament is the pricing function's rule, and it applies here too.
    assert admin.post("/api/admin/bookings", json=manual_body(slot.local_date, addons={"highlight_reel": 1})).status_code == 422
    assert db.scalar(select(func.count()).select_from(Booking)) == 0


# ---------- balance paid ----------


def test_balance_paid_stamps_and_undoes(db: Session, square_api: FakeSquareApi) -> None:
    _book(db)
    admin = admin_client()
    (booking,) = admin.get("/api/admin/bookings").json()
    assert booking["balance_paid_at"] is None

    marked = admin.post(f"/api/admin/bookings/{booking['id']}/balance-paid", json={"paid": True})
    assert marked.status_code == 200
    assert marked.json()["balance_paid_at"] is not None
    assert admin.get("/api/admin/bookings").json()[0]["balance_paid_at"] is not None

    undone = admin.post(f"/api/admin/bookings/{booking['id']}/balance-paid", json={"paid": False})
    assert undone.status_code == 200 and undone.json()["balance_paid_at"] is None
    assert admin.get("/api/admin/bookings").json()[0]["balance_paid_at"] is None

    assert square_api.requests == [], "marking the balance moves no money"
    assert admin.get("/api/admin/bookings").json()[0]["balance_cents"] == 44400, "the amount owed is unchanged"


def test_balance_paid_sends_nothing(db: Session) -> None:
    _book(db)
    admin = admin_client()
    (booking,) = admin.get("/api/admin/bookings").json()
    before = len(admin.get("/api/admin/notifications").json())
    assert admin.post(f"/api/admin/bookings/{booking['id']}/balance-paid", json={"paid": True}).status_code == 200
    assert len(admin.get("/api/admin/notifications").json()) == before


def test_balance_paid_is_refused_where_it_would_be_a_lie(db: Session) -> None:
    _book(db)
    admin = admin_client()
    (booking,) = admin.get("/api/admin/bookings").json()
    assert admin.post("/api/admin/bookings/999999/balance-paid", json={"paid": True}).status_code == 404
    assert admin.post(f"/api/admin/bookings/{booking['id']}/cancel", json={}).status_code == 200
    assert admin.post(f"/api/admin/bookings/{booking['id']}/balance-paid", json={"paid": True}).status_code == 409
    assert admin.get("/api/admin/bookings").json()[0]["balance_paid_at"] is None
