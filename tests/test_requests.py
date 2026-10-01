from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PartyRequest
from app.notifications import notify_new_request
from tests.conftest import FakeMessaging
from tests.helpers import client, client_from, make_booking, make_hold, make_slot, next_saturday, today


def next_weekday(days_ahead: int = 7) -> date:
    day = today() + timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def request_body(day: date, n: int = 1, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "date": day.isoformat(),
        "preferred_time": "16:00",
        "package": "standard",
        "kids": 18,
        "addons": {"pizza_pack": 1},
        "first_name": "Pat",
        "last_name": f"Parent{n}",
        "phone": f"(914) 555-{n:04d}",
        "email": f"parent{n}@example.com",
        "notes": "After-school party",
    }
    body.update(overrides)
    return body


def _notifications(engine: Engine) -> list[tuple[str, str, str, int | None]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT audience, channel, status, request_id FROM notifications ORDER BY channel")
        )
        return [tuple(row) for row in rows]


def test_requests(db: Session, engine: Engine) -> None:
    """The spec's stage-1 story: priced snapshot stored, owner notified once, no hold, no payment."""
    day = next_weekday()
    response = client_from("203.0.113.150").post("/api/requests", json=request_body(day, 1))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["total_cents"] == 54400
    assert body["time_label"] == "4:00 PM"

    stored = db.scalar(select(PartyRequest))
    assert stored is not None
    assert stored.status == "new"
    assert stored.total_cents == 54400
    assert stored.phone == "+19145550001"
    assert [item["key"] for item in stored.line_items] == ["package", "additional_kids", "pizza_pack"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM holds")).scalar_one() == 0

    rows = _notifications(engine)
    assert rows == [("owner", "sms", "logged", stored.id)]

    duplicate = client_from("203.0.113.151").post("/api/requests", json=request_body(day, 1))
    assert duplicate.status_code == 409
    same_email = client_from("203.0.113.152").post(
        "/api/requests", json=request_body(day, 2, email="parent1@example.com")
    )
    assert same_email.status_code == 409
    assert db.scalar(select(text("count(*)")).select_from(PartyRequest)) == 1


def test_owner_notice_content_and_discord(db: Session, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "discord_webhook_url", "https://discord.com/api/webhooks/1/abc")
    day = next_weekday()
    assert client_from("203.0.113.153").post("/api/requests", json=request_body(day, 3)).status_code == 201
    with engine.connect() as conn:
        rows = {row.channel: row for row in conn.execute(text("SELECT channel, status, body FROM notifications"))}
    assert set(rows) == {"sms", "discord"}
    assert rows["discord"].status == "sent"
    body = rows["sms"].body
    assert body.startswith("PARTY REQUEST: Standard")
    assert "at 4:00 PM, 18 kids, Pizza Pack x1. Total $544." in body
    assert "+19145550003" in body and "parent3@example.com" in body
    assert "After-school party" not in body, "notes stay out of the SMS"


def test_notify_replay_sends_nothing_twice(db: Session, engine: Engine) -> None:
    day = next_weekday()
    client_from("203.0.113.154").post("/api/requests", json=request_body(day, 4))
    request_id = db.scalar(select(PartyRequest.id))
    notify_new_request(db, get_settings(), request_id)
    notify_new_request(db, get_settings(), request_id)
    assert len(_notifications(engine)) == 1


def test_weekend_request_rejected_while_weekends_are_open(db: Session) -> None:
    response = client_from("203.0.113.155").post("/api/requests", json=request_body(next_saturday(7), 5))
    assert response.status_code == 422
    assert "booked online" in response.json()["detail"]


def test_request_mode_weekend_flow(db: Session, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "weekend_default", "request")
    saturday = next_saturday(7)
    make_slot(db, saturday)
    assert client_from("203.0.113.156").post("/api/requests", json=request_body(saturday, 6)).status_code == 201

    hold = client_from("203.0.113.157").post("/api/holds", json={"slot_id": 1})
    assert hold.status_code == 422
    assert "request-only" in hold.json()["detail"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM holds")).scalar_one() == 0


def test_request_mode_weekend_rejects_booked_and_blocked_dates(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "weekend_default", "request")
    booked = make_slot(db, next_saturday(7))
    make_booking(db, make_hold(db, booked, status="booked"), booked)
    blocked = make_slot(db, next_saturday(14))
    blocked.blocked_at = blocked.created_at
    db.commit()
    requester = client_from("203.0.113.158")
    assert requester.post("/api/requests", json=request_body(booked.local_date, 8)).status_code == 422
    assert requester.post("/api/requests", json=request_body(blocked.local_date, 9)).status_code == 422


def test_request_validation(db: Session) -> None:
    requester = client_from("203.0.113.159")
    soon = today() + timedelta(days=1)
    if soon.weekday() >= 5:
        soon = today()
    response = requester.post("/api/requests", json=request_body(soon, 10))
    assert response.status_code == 422
    assert "too soon" in response.json()["detail"]

    far = next_weekday(400)
    assert requester.post("/api/requests", json=request_body(far, 11)).status_code == 422

    day = next_weekday()
    assert requester.post("/api/requests", json=request_body(day, 12, preferred_time="07:00")).status_code == 422
    assert requester.post("/api/requests", json=request_body(day, 13, preferred_time="21:30")).status_code == 422
    assert requester.post("/api/requests", json=request_body(day, 14, phone="555-12")).status_code == 422
    assert requester.post("/api/requests", json=request_body(day, 15, addons={"highlight_reel": 1})).status_code == 422
    assert requester.post("/api/requests", json=request_body(day, 16, kids=31)).status_code == 422


def test_request_notification_failure_never_fails_the_request(
    db: Session, engine: Engine, messaging: "FakeMessaging", monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_from_number", "+15005550006")
    messaging.raise_error = RuntimeError("connection reset")
    response = client_from("203.0.113.160").post("/api/requests", json=request_body(next_weekday(), 17))
    assert response.status_code == 201, "the request is stored even when the owner text cannot be sent"
    assert _notifications(engine) == [("owner", "sms", "failed", response.json()["request_id"])]
