"""Hold expiry with no background process.

Nothing sweeps expired holds. Every surface that cares asks the same question at the moment it is asked:
is this hold still pending_payment and is its expires_at still in the future. These tests pin that the
answer is the same everywhere, that it needs no intervening request to become true, and that nothing in
the app is quietly running a timer to make it true.
"""

import pathlib
import re
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.main import app
from tests.helpers import checkout, client, client_from, make_hold, make_slot, next_saturday, start_hold


SCHEDULER_IMPORTS = re.compile(
    r"^\s*(?:import|from)\s+(apscheduler|celery|rq|schedule|threading|asyncio\.tasks)\b", re.MULTILINE
)


def test_the_app_starts_no_background_worker() -> None:
    """A daemon is the thing this design is avoiding, so its absence is asserted rather than assumed."""
    assert app.router.on_startup == []
    assert app.router.on_shutdown == []

    offenders = [
        path.name
        for path in sorted(pathlib.Path("app").rglob("*.py"))
        if SCHEDULER_IMPORTS.search(path.read_text())
    ]
    assert offenders == []


def test_a_lapsed_hold_is_dead_on_every_surface_at_once(db: Session) -> None:
    """One hold, lapsed with nothing else happening, then every surface asked in turn. No sweep ran in
    between, because there is nothing to run one."""
    slot = make_slot(db, next_saturday(7))
    hold = make_hold(db, slot, expires_in=timedelta(minutes=-1))

    grid = client().get("/api/availability").json()["days"]
    assert next(d["status"] for d in grid if d["date"] == slot.local_date.isoformat()) == "open"

    assert client().get(f"/api/holds/{hold.id}").json()["status"] == "expired"

    draft = client().patch(
        f"/api/holds/{hold.id}/draft", json={"package": "plus", "kids": 18, "addons": {}}
    )
    assert draft.status_code == 409
    assert draft.json()["status"] == "expired"

    paid = checkout(client(), str(hold.id))
    assert paid.status_code == 409
    assert paid.json()["status"] == "expired"

    # And the row itself was never touched by anything: it is still pending_payment, just out of time.
    assert db.execute(text("SELECT status FROM holds WHERE id = :id"), {"id": hold.id}).scalar_one() == "pending_payment"


def test_the_failed_payment_says_what_the_modal_needs_to_say(db: Session) -> None:
    """The funnel opens its "Still there?" modal off this response, so it needs the status, not just a 409."""
    slot = make_slot(db, next_saturday(7))
    hold = make_hold(db, slot, expires_in=timedelta(minutes=-1))
    body = checkout(client(), str(hold.id)).json()
    assert body["status"] == "expired"
    assert "no longer active" in body["detail"]


def test_re_holding_the_same_date_after_expiry_works(db: Session) -> None:
    """What the modal's Re-hold button does: ask for a fresh hold on the same slot, through the one
    endpoint that creates holds, so the same database constraint re-checks availability."""
    slot = make_slot(db, next_saturday(7))
    make_hold(db, slot, expires_in=timedelta(minutes=-1))

    funnel = client_from("203.0.113.90")
    again = start_hold(funnel, slot.id)
    assert again.status_code == 201
    # A full fifteen minutes, granted by the server rather than restarted by the client.
    assert 14 * 60 < again.json()["seconds_remaining"] <= 15 * 60


def test_re_holding_a_date_someone_else_took_fails_with_alternatives(db: Session) -> None:
    """Then the modal has only the way back to the grid, which is why the alternatives come with the 409."""
    slot = make_slot(db, next_saturday(7))
    make_hold(db, slot, expires_in=timedelta(minutes=-1))
    make_slot(db, next_saturday(14))

    assert start_hold(client_from("203.0.113.91"), slot.id).status_code == 201
    taken = start_hold(client_from("203.0.113.92"), slot.id)
    assert taken.status_code == 409
    assert taken.json()["alternatives"]


def test_an_expired_hold_frees_its_date_for_a_different_parent(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    mine = make_hold(db, slot, expires_in=timedelta(minutes=-1))
    assert start_hold(client_from("203.0.113.93"), slot.id).status_code == 201
    # The lapsed hold was marked expired on the way past, rather than by anything sweeping for it.
    assert db.execute(text("SELECT status FROM holds WHERE id = :id"), {"id": mine.id}).scalar_one() == "expired"


def test_expiry_needs_no_request_to_have_happened(db: Session) -> None:
    """The clock, not a visit, is what ends a hold: the very first read after the deadline sees it gone."""
    slot = make_slot(db, next_saturday(7))
    hold = make_hold(db, slot, expires_in=timedelta(seconds=1))
    db.execute(
        text("UPDATE holds SET expires_at = now() - interval '1 second' WHERE id = :id"), {"id": hold.id}
    )
    db.commit()
    grid = client().get("/api/availability").json()["days"]
    assert next(d["status"] for d in grid if d["date"] == slot.local_date.isoformat()) == "open"
