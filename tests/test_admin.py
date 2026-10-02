import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.models import Booking
from tests.conftest import FakeSquareApi
from tests.helpers import (
    admin_client,
    client,
    client_from,
    create_hold_via_api,
    deliver,
    make_slot,
    next_saturday,
    payment_event,
    start_hold,
)


def _book(db: Session, days_ahead: int = 7, n: int = 1) -> tuple[str, str]:
    hold_id, ref = create_hold_via_api(db, days_ahead, n)
    deliver(payment_event(ref, payment_id=f"pay-{hold_id}"))
    code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]
    return hold_id, code


def manual_body(slot_id: int, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "slot_id": slot_id,
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
    assert anonymous.post("/api/admin/bookings", json=manual_body(1)).status_code == 401
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
    created = admin.post("/api/admin/bookings", json=manual_body(slot.id, total_cents=1, deposit_cents=1))
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
    assert admin.post("/api/admin/bookings", json=manual_body(slot.id, deposit_collected=False)).status_code == 201
    (booking,) = admin.get("/api/admin/bookings").json()
    assert booking["paid_amount_cents"] == 0
    assert booking["deposit_status"] == "waived"
    assert booking["balance_cents"] == booking["total_cents"], "nothing was paid, so the whole total is owed"


def test_manual_booking_claims_the_date(db: Session) -> None:
    slot = make_slot(db, next_saturday(9))
    assert admin_client().post("/api/admin/bookings", json=manual_body(slot.id)).status_code == 201
    taken = start_hold(client_from("203.0.113.77"), slot.id)
    assert taken.status_code == 409, "the date is gone for parents too"
    days = {day["slot_id"]: day["status"] for day in client().get("/api/availability").json()["days"]}
    assert days[slot.id] == "booked"


def test_manual_booking_refuses_a_date_that_is_not_free(db: Session) -> None:
    admin = admin_client()

    held = make_slot(db, next_saturday(9))
    assert start_hold(client_from("203.0.113.78"), held.id).status_code == 201
    assert admin.post("/api/admin/bookings", json=manual_body(held.id)).status_code == 409

    blocked = make_slot(db, next_saturday(16))
    assert admin.post(f"/api/admin/slots/{blocked.id}/block").status_code == 200
    assert admin.post("/api/admin/bookings", json=manual_body(blocked.id)).status_code == 409

    booked = make_slot(db, next_saturday(23))
    assert admin.post("/api/admin/bookings", json=manual_body(booked.id)).status_code == 201
    assert admin.post("/api/admin/bookings", json=manual_body(booked.id)).status_code == 409

    assert admin.post("/api/admin/bookings", json=manual_body(999999)).status_code == 404
    assert db.scalar(select(func.count()).select_from(Booking)) == 1, "only the one that was allowed through"


def test_manual_booking_loses_a_race_without_double_booking(db: Session) -> None:
    """The date goes between the form loading and the submit. The unique index decides, not the pre-check."""
    slot = make_slot(db, next_saturday(9))
    barrier = threading.Barrier(2)

    def attempt(n: int) -> httpx.Response:
        admin = admin_client()
        barrier.wait()
        return admin.post("/api/admin/bookings", json=manual_body(slot.id, email=f"race{n}@example.com"))

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
    assert admin.post("/api/admin/bookings", json=manual_body(slot.id)).status_code == 201
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
    assert admin.post("/api/admin/bookings", json=manual_body(slot.id)).status_code == 201
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
    assert admin.post("/api/admin/bookings", json=manual_body(slot.id, phone="555")).status_code == 422
    assert admin.post("/api/admin/bookings", json=manual_body(slot.id, package="deluxe")).status_code == 422
    assert admin.post("/api/admin/bookings", json=manual_body(slot.id, kids=99)).status_code == 422
    # Highlight Reel without Tournament is the pricing function's rule, and it applies here too.
    assert admin.post("/api/admin/bookings", json=manual_body(slot.id, addons={"highlight_reel": 1})).status_code == 422
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
