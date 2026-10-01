from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.helpers import checkout, client, client_from, deliver, make_slot, next_saturday, payment_event, start_hold


def _expire(engine: Engine, hold_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": hold_id})


def test_expired_hold_payment(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.50")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    assert checkout(funnel, hold_id, 1).status_code == 200
    _expire(engine, hold_id)

    response = deliver(payment_event(f"order-{hold_id}", payment_id="pay-late"))
    assert response.json() == {"outcome": "expired_hold"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bookings")).scalar_one() == 0
        flag = conn.execute(text("SELECT kind, hold_id::text, amount_cents FROM payment_flags")).one()
        assert tuple(flag) == ("paid_after_expiry", hold_id, 10000)
    assert client().get(f"/api/holds/{hold_id}").json()["status"] == "paid_after_expiry"
    statuses = {s["slot_id"]: s["status"] for s in client().get("/api/availability").json()["days"]}
    assert statuses[slot.id] == "open", "a late payment does not take the slot"


def test_late_payment_after_someone_else_took_the_slot(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    loser = client_from("203.0.113.51")
    first = start_hold(loser, slot.id).json()["hold_id"]
    assert checkout(loser, first, 1).status_code == 200
    _expire(engine, first)
    winner = client_from("203.0.113.52")
    second = start_hold(winner, slot.id)
    assert second.status_code == 201
    assert checkout(winner, second.json()["hold_id"], 2).status_code == 200

    assert deliver(payment_event(f"order-{first}", payment_id="pay-late")).json() == {"outcome": "expired_hold"}
    assert client().get(f"/api/holds/{first}").json()["status"] == "paid_after_expiry"
    assert client().get(f"/api/holds/{second.json()['hold_id']}").json()["status"] == "pending_payment"

    assert deliver(payment_event(f"order-{second.json()['hold_id']}", payment_id="pay-on-time")).json() == {
        "outcome": "booked"
    }
