from urllib.parse import parse_qs

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine
from app.services.booking import handle_square_webhook
from tests.conftest import FakeMessaging, FakeSmtp
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


@pytest.fixture
def smtp_on(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.test.example")
    monkeypatch.setattr(settings, "smtp_username", "mailer")
    monkeypatch.setattr(settings, "smtp_password", "mail-secret")
    monkeypatch.setattr(settings, "email_from", "Encore Esports <bookings@encore-test.example>")


def test_tests_never_use_real_twilio_or_smtp() -> None:
    assert not get_settings().twilio_enabled
    assert not get_settings().email_enabled


def test_sms_templates_match_spec(db: Session, engine: Engine) -> None:
    hold_id, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]
    owner, _, parent = _rows(engine)
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
    rows = {(row["audience"], row["channel"]): row for row in _rows(engine)}
    assert rows[("parent", "sms")]["status"] == rows[("owner", "sms")]["status"] == "sent"
    assert rows[("parent", "email")]["status"] == "logged"
    assert str(rows[("parent", "sms")]["provider_message_id"]).startswith("SM")
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
    rows = [row for row in _rows(engine) if row["channel"] == "sms"]
    assert [row["status"] for row in rows] == ["failed", "failed"]
    assert "21608" in str(rows[0]["error"])


def test_network_error_never_fails_the_booking(db: Session, engine: Engine, messaging: FakeMessaging, twilio_on: None) -> None:
    _, order_id = create_hold_via_api(db)
    messaging.raise_error = RuntimeError("connection reset")
    assert deliver(payment_event(order_id)).json() == {"outcome": "booked"}
    assert [(row["channel"], row["status"]) for row in _rows(engine)] == [
        ("sms", "failed"),
        ("email", "logged"),
        ("sms", "failed"),
    ]


def test_discord_owner_post(db: Session, engine: Engine, messaging: FakeMessaging, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "discord_webhook_url", "https://discord.com/api/webhooks/1/abc")
    _, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    rows = _rows(engine)
    assert [(row["audience"], row["channel"], row["status"]) for row in rows] == [
        ("owner", "discord", "sent"),
        ("owner", "sms", "logged"),
        ("parent", "email", "logged"),
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
    assert len(_rows(engine)) == 3
    deliver(raw)
    assert len(_rows(engine)) == 3


def test_no_owner_sms_without_owner_phone(db: Session, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "owner_phone", "")
    _, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    assert [(row["audience"], row["channel"]) for row in _rows(engine)] == [("parent", "email"), ("parent", "sms")]


def test_money_formatting() -> None:
    from app.notifications import addons_summary, dollars

    assert dollars(44400) == "$444"
    assert dollars(123450) == "$1,234.50"
    assert addons_summary({}) == "no add-ons"
    assert addons_summary({"tournament": 1, "pizza_pack": 2}) == "Tournament + Livestream, Pizza Pack x2"


def _confirmed_email_setup(db: Session) -> tuple[str, str]:
    hold_id, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]
    return hold_id, code


def test_email_body_has_everything_the_parent_needs(db: Session, engine: Engine) -> None:
    _, code = _confirmed_email_setup(db)
    (email,) = [row for row in _rows(engine) if row["channel"] == "email"]
    day = next_saturday(7)
    body = str(email["body"])
    assert email["recipient"] == "parent1@example.com"
    assert email["status"] == "logged"
    assert email["sent_at"] is not None
    assert email["provider_message_id"] is None
    for expected in (
        "Hi Pat,",
        f"Confirmation code: {code}",
        f"Confirmation page: http://testserver/confirm.html?code={code}",
        f"When: {day:%a}, {day:%b} {day.day} at 10:00 AM",
        "Package: Standard, 18 kids",
        "  Standard package, up to 15 kids, 3 hours: $399",
        "  Additional kids x3 ($35 each): $105",
        "  Pizza Pack: $40",
        "  Total: $544",
        "  Deposit received: $100",
        "  Balance due day of party: $444",
        "538 Main St, 2nd floor, entrance on Division St.",
        "Cancel or reschedule free up to 7 days before your party.",
        "you can reschedule once",
        "(646) 203-8087",
    ):
        assert expected in body, expected


def test_email_is_sent_over_smtp_when_configured(db: Session, engine: Engine, smtp: FakeSmtp, smtp_on: None) -> None:
    _confirmed_email_setup(db)
    (email,) = [row for row in _rows(engine) if row["channel"] == "email"]
    assert email["status"] == "sent"
    assert str(email["provider_message_id"]).endswith("@encore-test.example>")
    assert smtp.connections == [("smtp.test.example", 587)]
    assert smtp.starttls_calls == 1
    assert smtp.logins == [("mailer", "mail-secret")]
    (message,) = smtp.messages
    assert message["To"] == "parent1@example.com"
    assert message["From"] == "Encore Esports <bookings@encore-test.example>"
    day = next_saturday(7)
    assert message["Subject"] == f"You're booked: Standard party {day:%a}, {day:%b} {day.day}"
    assert message.get_content().strip() == str(email["body"]).strip()


def test_smtp_failure_never_fails_the_booking(db: Session, engine: Engine, smtp: FakeSmtp, smtp_on: None) -> None:
    _, order_id = create_hold_via_api(db)
    smtp.fail_with = ConnectionRefusedError("smtp down")
    response = deliver(payment_event(order_id))
    assert response.status_code == 200
    assert response.json() == {"outcome": "booked"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bookings")).scalar_one() == 1
    email = next(row for row in _rows(engine) if row["channel"] == "email")
    assert email["status"] == "failed"
    assert "smtp down" in str(email["error"])
    sms = [row for row in _rows(engine) if row["channel"] == "sms"]
    assert [row["status"] for row in sms] == ["logged", "logged"], "a failed email does not stop the texts"


def test_email_is_never_sent_twice(db: Session, engine: Engine, smtp: FakeSmtp, smtp_on: None) -> None:
    _, order_id = create_hold_via_api(db)
    raw = payment_event(order_id, event_id="evt-email-once")
    deliver(raw)
    deliver(raw)
    deliver(payment_event(order_id, event_id="evt-email-once-2", event_type="payment.created"))
    assert len(smtp.messages) == 1
    assert len([row for row in _rows(engine) if row["channel"] == "email"]) == 1


def test_no_email_for_an_expired_hold_payment(db: Session, engine: Engine, smtp: FakeSmtp, smtp_on: None) -> None:
    hold_id, order_id = create_hold_via_api(db)
    with engine.begin() as conn:
        conn.execute(text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": hold_id})
    deliver(payment_event(order_id))
    assert smtp.messages == []
    assert all(row["channel"] != "email" for row in _rows(engine))


def test_reminder_and_review_templates_exist_for_the_scheduler(db: Session) -> None:
    """Stubs per SPEC: written and tested now, sent by a scheduled job after go-live."""
    from sqlalchemy import select

    from app.models import Booking
    from app.notifications import parent_reminder_sms, parent_review_sms

    _, order_id = create_hold_via_api(db)
    deliver(payment_event(order_id))
    booking = db.scalars(select(Booking)).one()
    day = next_saturday(7)
    reminder = parent_reminder_sms(booking)
    assert reminder.startswith(f"See you {day:%a}, {day:%b} {day.day} 10:00 AM!")
    assert "Balance $444 due that day" in reminder
    assert "538 Main St" in reminder
    review = parent_review_sms(booking, "https://g.page/r/test/review")
    assert "Thanks for celebrating Sam" in review
    assert review.endswith("https://g.page/r/test/review")
