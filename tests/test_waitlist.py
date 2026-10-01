from datetime import timedelta

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.helpers import admin_client, client, client_from, make_slot, next_saturday, start_hold


def test_waitlist_signup_is_email_only(engine: Engine) -> None:
    response = client_from("203.0.113.90").post("/api/waitlist", json={"email": "Waiting@Example.com"})
    assert response.status_code == 201
    assert "on the list" in response.json()["detail"]
    with engine.connect() as conn:
        row = conn.execute(text("SELECT email, host(client_ip) FROM waitlist")).one()
    assert tuple(row) == ("waiting@example.com", "203.0.113.90")


def test_duplicate_signup_is_silent(engine: Engine) -> None:
    for _ in range(2):
        response = client().post("/api/waitlist", json={"email": "waiting@example.com"})
        assert response.status_code == 201, "a duplicate neither errors nor reveals it was already there"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM waitlist")).scalar_one() == 1


def test_waitlist_rejects_bad_email() -> None:
    assert client().post("/api/waitlist", json={"email": "not-an-email"}).status_code == 422


def test_admin_leads_show_waitlist_and_abandoned_checkouts(db: Session, engine: Engine) -> None:
    client().post("/api/waitlist", json={"email": "waiting@example.com"})
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("203.0.113.91")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    funnel.patch(f"/api/holds/{hold_id}/email", json={"email": "abandoner@example.com"})
    with engine.begin() as conn:
        conn.execute(text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": hold_id})
    anonymous = start_hold(client_from("203.0.113.92"), make_slot(db, next_saturday(14)).id).json()["hold_id"]
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": anonymous}
        )

    body = admin_client().get("/api/admin/leads").json()
    assert [row["email"] for row in body["waitlist"]] == ["waiting@example.com"]
    assert [row["email"] for row in body["abandoned"]] == ["abandoner@example.com"]
    abandoned = body["abandoned"][0]
    assert abandoned["hold_id"] == hold_id
    assert abandoned["package"] is None, "the funnel was abandoned before a package was chosen"
    assert abandoned["day_label"]


def test_leads_require_admin() -> None:
    assert client().get("/api/admin/leads").status_code == 401
