from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.helpers import client, client_from, deliver, hold_body, make_slot, next_saturday, payment_event


def _expire(engine: Engine, hold_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": hold_id})


def test_expired_hold_payment(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    hold_id = client_from("203.0.113.50").post("/api/holds", json=hold_body(slot.id, 1)).json()["hold_id"]
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
    first = client_from("203.0.113.51").post("/api/holds", json=hold_body(slot.id, 1)).json()["hold_id"]
    _expire(engine, first)
    second = client_from("203.0.113.52").post("/api/holds", json=hold_body(slot.id, 2))
    assert second.status_code == 201

    assert deliver(payment_event(f"order-{first}", payment_id="pay-late")).json() == {"outcome": "expired_hold"}
    assert client().get(f"/api/holds/{first}").json()["status"] == "paid_after_expiry"
    assert client().get(f"/api/holds/{second.json()['hold_id']}").json()["status"] == "pending_payment"

    assert deliver(payment_event(f"order-{second.json()['hold_id']}", payment_id="pay-on-time")).json() == {
        "outcome": "booked"
    }
