import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.helpers import client, create_hold_via_api, deliver, payment_event


def _count(engine: Engine, sql: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


def _notifications(engine: Engine) -> list[tuple[str, str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT audience, channel, status FROM notifications ORDER BY audience, channel"))
        return [tuple(row) for row in rows]


def test_webhook_replay(db: Session, engine: Engine) -> None:
    hold_id, order_id = create_hold_via_api(db)
    raw = payment_event(order_id, payment_id="pay-replay", event_id="evt-1")
    assert deliver(raw).json() == {"outcome": "booked"}
    assert deliver(raw).json() == {"outcome": "duplicate"}
    assert _count(engine, "SELECT count(*) FROM bookings") == 1
    assert _count(engine, "SELECT count(*) FROM square_events") == 1
    assert _count(engine, "SELECT count(*) FROM payment_flags") == 0
    assert _notifications(engine) == [
        ("owner", "sms", "logged"),
        ("parent", "email", "logged"),
        ("parent", "sms", "logged"),
    ]
    body = client().get(f"/api/holds/{hold_id}").json()
    assert body["status"] == "booked"
    assert body["party"]["confirmation_code"]


def test_created_then_updated_for_same_payment_books_once(db: Session, engine: Engine) -> None:
    _, order_id = create_hold_via_api(db)
    created = payment_event(order_id, payment_id="pay-2", event_id="evt-created", event_type="payment.created")
    updated = payment_event(order_id, payment_id="pay-2", event_id="evt-updated", event_type="payment.updated")
    assert deliver(created).json() == {"outcome": "booked"}
    assert deliver(updated).json() == {"outcome": "duplicate"}
    assert _count(engine, "SELECT count(*) FROM bookings") == 1
    assert _count(engine, "SELECT count(*) FROM square_events") == 2
    assert _count(engine, "SELECT count(*) FROM payment_flags") == 0
    assert _notifications(engine) == [
        ("owner", "sms", "logged"),
        ("parent", "email", "logged"),
        ("parent", "sms", "logged"),
    ]


def test_simultaneous_deliveries_book_once(db: Session, engine: Engine) -> None:
    """Same payment, several event ids and a repeated one, all at once on separate connections."""
    _, order_id = create_hold_via_api(db)
    event_ids = ["evt-a", "evt-a", "evt-b", "evt-c", "evt-c", "evt-d"]
    barrier = threading.Barrier(len(event_ids))

    def send(event_id: str) -> str:
        raw = payment_event(order_id, payment_id="pay-3", event_id=event_id)
        barrier.wait()
        return deliver(raw).json()["outcome"]

    with ThreadPoolExecutor(len(event_ids)) as pool:
        outcomes = list(pool.map(send, event_ids))
    assert sorted(outcomes) == ["booked"] + ["duplicate"] * (len(event_ids) - 1)
    assert _count(engine, "SELECT count(*) FROM bookings") == 1
    assert _count(engine, "SELECT count(*) FROM payment_flags") == 0
    assert _notifications(engine) == [
        ("owner", "sms", "logged"),
        ("parent", "email", "logged"),
        ("parent", "sms", "logged"),
    ]


def test_second_different_payment_on_booked_order_is_flagged(db: Session, engine: Engine) -> None:
    _, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id, payment_id="pay-first"))
    assert deliver(payment_event(order_id, payment_id="pay-second")).json() == {"outcome": "duplicate_payment"}
    assert _count(engine, "SELECT count(*) FROM bookings") == 1
    assert _count(engine, "SELECT count(*) FROM payment_flags WHERE kind = 'duplicate_payment'") == 1


def test_wrong_amount_is_flagged_not_booked(db: Session, engine: Engine) -> None:
    _, order_id = create_hold_via_api(db)
    assert deliver(payment_event(order_id, amount=9999)).json() == {"outcome": "amount_mismatch"}
    assert deliver(payment_event(order_id, payment_id="pay-cad", currency="CAD")).json() == {"outcome": "amount_mismatch"}
    assert _count(engine, "SELECT count(*) FROM bookings") == 0
    assert _count(engine, "SELECT count(*) FROM payment_flags WHERE kind = 'amount_mismatch'") == 2


def test_unknown_order_is_flagged(engine: Engine) -> None:
    assert deliver(payment_event(f"order-{uuid.uuid4()}")).json() == {"outcome": "unmatched"}
    assert _count(engine, "SELECT count(*) FROM payment_flags WHERE kind = 'unmatched_order'") == 1
