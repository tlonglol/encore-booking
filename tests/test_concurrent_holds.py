import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Hold
from tests.helpers import client_from, hold_body, make_slot, next_saturday

PARALLEL_REQUESTS = 10


def test_concurrent_holds(db: Session) -> None:
    """N simultaneous requests, each on its own connection and with its own phone, email, and IP: one 201, N-1 409s."""
    slot = make_slot(db, next_saturday(7))
    barrier = threading.Barrier(PARALLEL_REQUESTS)

    def attempt(n: int) -> httpx.Response:
        client = client_from(f"203.0.113.{n + 1}")
        body = hold_body(slot.id, n)
        barrier.wait()
        return client.post("/api/holds", json=body)

    with ThreadPoolExecutor(PARALLEL_REQUESTS) as pool:
        responses = list(pool.map(attempt, range(PARALLEL_REQUESTS)))

    codes = sorted(response.status_code for response in responses)
    assert codes == [201] + [409] * (PARALLEL_REQUESTS - 1), [r.text for r in responses]
    for response in responses:
        if response.status_code == 409:
            alternatives = response.json()["alternatives"]
            assert alternatives, "the loser is offered other dates"
            assert slot.id not in {alt["slot_id"] for alt in alternatives}
            assert {alt["status"] for alt in alternatives} == {"open"}
    active = db.scalar(select(func.count()).select_from(Hold).where(Hold.slot_id == slot.id, Hold.status == "pending_payment"))
    assert active == 1


def _insert_pending_hold(conn: object, slot_id: int, n: int) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO holds (slot_id, status, expires_at, package, kids, addons, line_items, total_cents,"
            " deposit_cents, first_name, last_name, phone, email)"
            " VALUES (:slot_id, 'pending_payment', now() + interval '15 minutes', 'standard', 10, '{}', '[]',"
            " 39900, 10000, 'A', 'B', :phone, :email)"
        ),
        {"slot_id": slot_id, "phone": f"+1914555{n:04d}", "email": f"p{n}@example.com"},
    )


def test_losing_insert_waits_for_the_winner_then_fails(db: Session, engine: Engine) -> None:
    """The exact interleaving from the design: the second insert blocks on the first, then gets unique_violation."""
    slot = make_slot(db, next_saturday(7))
    outcome: dict[str, BaseException | None] = {}

    winner = engine.connect()
    winner.begin()
    _insert_pending_hold(winner, slot.id, 1)

    def loser() -> None:
        with engine.connect() as conn:
            try:
                with conn.begin():
                    _insert_pending_hold(conn, slot.id, 2)
                outcome["error"] = None
            except IntegrityError as error:
                outcome["error"] = error

    thread = threading.Thread(target=loser)
    thread.start()
    deadline = time.monotonic() + 5
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            waiting = observer.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                    " AND wait_event_type = 'Lock' AND query LIKE 'INSERT INTO holds%'"
                )
            ).scalar_one()
            if waiting:
                break
            time.sleep(0.02)
        else:
            pytest.fail("the second insert never blocked on the first")
    assert thread.is_alive(), "the loser is still waiting while the winner is uncommitted"
    winner.commit()
    winner.close()
    thread.join(timeout=5)
    assert isinstance(outcome["error"], IntegrityError)
    assert "holds_one_active_per_slot" in str(outcome["error"])
