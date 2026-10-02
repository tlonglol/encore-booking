"""Waiting on a specific date.

The old waitlist took an email and nothing else, so the owner learned that someone wanted a party but
never which weekend. Every test here is about the date travelling with the signup, because a signup
that cannot be acted on is not worth storing.
"""

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.helpers import admin_client, client, client_from, make_slot, next_saturday, start_hold


def join(test_client, slot_id, email="waiting@example.com", phone=None):  # type: ignore[no-untyped-def]
    body = {"slot_id": slot_id, "email": email}
    if phone is not None:
        body["phone"] = phone
    return test_client.post("/api/waitlist", json=body)


def test_a_signup_stores_the_date_it_is_waiting_for(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    response = join(client_from("203.0.113.90"), slot.id, email="Waiting@Example.com", phone="(914) 555-0123")
    assert response.status_code == 201
    assert "opens" in response.json()["detail"]

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT slot_id, email, phone, host(client_ip) AS ip, notified_at FROM waitlist")
        ).one()
    assert row.slot_id == slot.id
    assert row.email == "waiting@example.com", "normalized, so the same person twice is one row"
    assert row.phone == "+19145550123", "stored E.164 like every other phone on the system"
    assert row.ip == "203.0.113.90"
    assert row.notified_at is None, "nothing notifies yet; the column waits for that to be built"


def test_the_phone_is_optional(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    assert join(client(), slot.id).status_code == 201
    with engine.connect() as conn:
        assert conn.execute(text("SELECT phone FROM waitlist")).scalar_one() is None


def test_a_phone_that_is_not_a_phone_is_refused(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    assert join(client(), slot.id, phone="banana").status_code == 422


def test_the_same_person_on_the_same_date_twice_is_one_row(db: Session, engine: Engine) -> None:
    slot = make_slot(db, next_saturday(7))
    first = join(client(), slot.id)
    second = join(client(), slot.id)
    assert first.status_code == 201 and second.status_code == 201
    assert second.json() == first.json(), "a repeat reveals nothing about whether it was already there"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM waitlist")).scalar_one() == 1


def test_the_same_person_on_two_dates_is_two_rows(db: Session, engine: Engine) -> None:
    """Two different asks, not a duplicate: they would take either Saturday."""
    first = make_slot(db, next_saturday(7))
    second = make_slot(db, next_saturday(14))
    assert join(client(), first.id).status_code == 201
    assert join(client(), second.id).status_code == 201
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM waitlist")).scalar_one() == 2


def test_a_signup_with_no_date_is_refused() -> None:
    """The shape the old blind form used. It must not be possible any more."""
    assert client().post("/api/waitlist", json={"email": "waiting@example.com"}).status_code == 422


def test_a_signup_for_a_date_that_does_not_exist_is_refused(engine: Engine) -> None:
    assert join(client(), 999999).status_code == 404
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM waitlist")).scalar_one() == 0


def test_a_bad_email_is_refused(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    assert join(client(), slot.id, email="not-an-email").status_code == 422


def test_signups_are_rate_limited_per_address(db: Session, engine: Engine) -> None:
    """Same gentle shape as the hold caps: a rolling hour per address."""
    flood = client_from("203.0.113.99")
    slots = [make_slot(db, next_saturday(7 + 7 * n)) for n in range(12)]
    codes = [join(flood, slot.id, email=f"waiting{n}@example.com").status_code for n, slot in enumerate(slots)]
    assert 429 in codes, "the flood is stopped"
    assert codes[0] == 201, "and the first one through was fine"

    # A different address is unaffected by someone else's flood.
    assert join(client_from("198.51.100.5"), slots[0].id, email="elsewhere@example.com").status_code == 201


def test_a_date_that_lapses_back_to_open_still_accepts_a_signup(db: Session) -> None:
    """The grid can be a few seconds stale. Refusing here would refuse exactly the person this is for."""
    slot = make_slot(db, next_saturday(7))
    assert join(client(), slot.id).status_code == 201, "no availability check stands between them and the list"


# ---- what the owner sees ----


def test_admin_waitlist_shows_the_date_and_a_count_per_date(db: Session) -> None:
    busy = make_slot(db, next_saturday(7))
    quiet = make_slot(db, next_saturday(14))
    for n in range(3):
        assert join(client_from(f"198.51.100.{20 + n}"), busy.id, email=f"p{n}@example.com").status_code == 201
    assert join(client_from("198.51.100.30"), quiet.id, email="only@example.com").status_code == 201

    rows = admin_client().get("/api/admin/waitlist").json()
    assert len(rows) == 4
    by_date = {row["date"]: row["waiting_on_this_date"] for row in rows}
    assert by_date[busy.local_date.isoformat()] == 3, "the sold-out Saturday is visibly in demand"
    assert by_date[quiet.local_date.isoformat()] == 1
    first = next(row for row in rows if row["date"] == busy.local_date.isoformat())
    assert first["day_label"] and first["email"] and first["notified_at"] is None


def test_the_waitlist_requires_admin(db: Session) -> None:
    assert client().get("/api/admin/waitlist").status_code == 401


def test_leads_are_abandoned_checkouts_only(db: Session, engine: Engine) -> None:
    """The waitlist moved to its own view, because it now has a shape leads does not: a date."""
    slot = make_slot(db, next_saturday(7))
    join(client(), slot.id)
    funnel = client_from("203.0.113.91")
    hold_id = start_hold(funnel, make_slot(db, next_saturday(14)).id).json()["hold_id"]
    funnel.patch(f"/api/holds/{hold_id}/email", json={"email": "abandoner@example.com"})
    with engine.begin() as conn:
        conn.execute(text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": hold_id})

    body = admin_client().get("/api/admin/leads").json()
    assert "waitlist" not in body
    assert [row["email"] for row in body["abandoned"]] == ["abandoner@example.com"]


def test_leads_require_admin() -> None:
    assert client().get("/api/admin/leads").status_code == 401
