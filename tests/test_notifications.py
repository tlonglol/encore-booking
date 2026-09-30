from urllib.parse import parse_qs

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine
from app.services.booking import handle_square_webhook
from tests.conftest import FakeMessaging
from tests.helpers import (
    WEBHOOK_KEY,
    WEBHOOK_URL,
    client,
    create_hold_via_api,
    deliver,
    next_saturday,
    payment_event,
)


def _rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT audience, channel, recipient, body, status, error, provider_message_id, sent_at"
                 " FROM notifications ORDER BY audience, channel")
        )
        return [dict(row._mapping) for row in result]


@pytest.fixture
def twilio_on(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_from_number", "+15005550006")


def test_tests_never_use_real_twilio() -> None:
    assert not get_settings().twilio_enabled


def test_sms_templates_match_spec(db: Session, engine: Engine) -> None:
    hold_id, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]
    owner, parent = _rows(engine)
    day = next_saturday(7)
    date_label = f"{day:%a}, {day:%b} {day.day}"
    assert parent["recipient"] == "+19145550001"
    assert parent["body"] == (
        f"You're booked! Standard party {date_label} 10:00 AM at Encore Esports, 18 kids. $100 deposit received, "
        "balance $444 due day of party. 538 Main St, 2nd floor, entrance on Division St. "
        f"Questions: (646) 203-8087. Details: http://testserver/confirm.html?code={code}"
    )
    assert owner["recipient"] == "+19145559999"
    assert owner["body"] == (
        f"NEW PARTY: Standard {date_label} 10:00 AM, 18 kids, Pizza Pack x1. Deposit paid. Pat Parent1 +19145550001."
    )
    assert parent["status"] == owner["status"] == "logged"
    assert parent["sent_at"] is not None


def test_twilio_send_is_recorded(db: Session, engine: Engine, messaging: FakeMessaging, twilio_on: None) -> None:
    _, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    assert [row["status"] for row in _rows(engine)] == ["sent", "sent"]
    assert all(str(row["provider_message_id"]).startswith("SM") for row in _rows(engine))
    sent = messaging.requests[0]
    assert sent.url.path == "/2010-04-01/Accounts/ACtest/Messages.json"
    form = parse_qs(sent.content.decode())
    assert form["From"] == ["+15005550006"]
    assert sent.headers["authorization"].startswith("Basic ")


def test_twilio_failure_never_fails_the_booking(db: Session, engine: Engine, messaging: FakeMessaging, twilio_on: None) -> None:
    _, order_id = create_hold_via_api(db)
    messaging.twilio_status = 400
    response = deliver(payment_event(order_id))
    assert response.status_code == 200
    assert response.json() == {"outcome": "booked"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bookings")).scalar_one() == 1
    rows = _rows(engine)
    assert [row["status"] for row in rows] == ["failed", "failed"]
    assert "21608" in str(rows[0]["error"])


def test_network_error_never_fails_the_booking(db: Session, engine: Engine, messaging: FakeMessaging, twilio_on: None) -> None:
    _, order_id = create_hold_via_api(db)
    messaging.raise_error = RuntimeError("connection reset")
    assert deliver(payment_event(order_id)).json() == {"outcome": "booked"}
    assert [row["status"] for row in _rows(engine)] == ["failed", "failed"]


def test_discord_owner_post(db: Session, engine: Engine, messaging: FakeMessaging, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "discord_webhook_url", "https://discord.com/api/webhooks/1/abc")
    _, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    rows = _rows(engine)
    assert [(row["audience"], row["channel"], row["status"]) for row in rows] == [
        ("owner", "discord", "sent"),
        ("owner", "sms", "logged"),
        ("parent", "sms", "logged"),
    ]
    assert rows[0]["recipient"] == "discord", "the webhook URL is a secret and is never stored"
    (request,) = messaging.requests
    assert request.url.params["wait"] == "true"


def test_discord_failure_is_recorded(db: Session, engine: Engine, messaging: FakeMessaging, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "discord_webhook_url", "https://discord.com/api/webhooks/1/abc")
    messaging.discord_status = 404
    _, order_id = create_hold_via_api(db)
    assert deliver(payment_event(order_id)).json() == {"outcome": "booked"}
    assert _rows(engine)[0]["status"] == "failed"


def test_owner_alerted_on_late_payment_and_parent_is_not(db: Session, engine: Engine) -> None:
    hold_id, order_id = create_hold_via_api(db)
    with engine.begin() as conn:
        conn.execute(text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": hold_id})
    deliver(payment_event(order_id))
    (owner,) = _rows(engine)
    assert owner["audience"] == "owner"
    assert str(owner["body"]).startswith("PAYMENT ON EXPIRED HOLD, call parent: Standard")
    assert "date NOT confirmed" in str(owner["body"])


def test_redelivery_heals_a_crash_between_commit_and_send(db: Session, engine: Engine) -> None:
    _, order_id = create_hold_via_api(db)
    raw = payment_event(order_id, event_id="evt-crash")
    from app.services.square import signature_for

    with Session(get_engine()) as session:
        result = handle_square_webhook(session, get_settings(), raw, signature_for(raw, WEBHOOK_KEY, WEBHOOK_URL))
    assert result.outcome == "booked"
    assert _rows(engine) == [], "process died before notifying"
    assert deliver(raw).json() == {"outcome": "duplicate"}
    assert len(_rows(engine)) == 2
    deliver(raw)
    assert len(_rows(engine)) == 2


def test_no_owner_sms_without_owner_phone(db: Session, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "owner_phone", "")
    _, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    assert [row["audience"] for row in _rows(engine)] == ["parent"]


def test_money_formatting() -> None:
    from app.notifications import addons_summary, dollars

    assert dollars(44400) == "$444"
    assert dollars(123450) == "$1,234.50"
    assert addons_summary({}) == "no add-ons"
    assert addons_summary({"tournament": 1, "pizza_pack": 2}) == "Tournament + Livestream, Pizza Pack x2"


