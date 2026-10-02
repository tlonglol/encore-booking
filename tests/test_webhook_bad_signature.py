from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.helpers import WEBHOOK_KEY, WEBHOOK_URL, create_hold_via_api, deliver, payment_event


def _state(engine: Engine) -> tuple[int, int, int, list[str]]:
    with engine.connect() as conn:
        return (
            conn.execute(text("SELECT count(*) FROM bookings")).scalar_one(),
            conn.execute(text("SELECT count(*) FROM square_events")).scalar_one(),
            conn.execute(text("SELECT count(*) FROM payment_flags")).scalar_one(),
            list(conn.execute(text("SELECT status FROM holds ORDER BY id")).scalars()),
        )


def test_webhook_bad_signature(db: Session, engine: Engine) -> None:
    _, ref = create_hold_via_api(db)
    before = _state(engine)
    raw = payment_event(ref)
    tampered = raw.replace(b'"amount": 10000', b'"amount": 10001')
    from app.services.square import signature_for

    cases = {
        "tampered body": deliver(tampered, signature=signature_for(raw, WEBHOOK_KEY, WEBHOOK_URL)),
        "wrong key": deliver(raw, key="not-the-key"),
        "wrong notification url": deliver(raw, url="https://attacker.example.com/api/webhooks/square"),
        "missing header": deliver(raw, signature=""),
        "garbage header": deliver(raw, signature="bm90IGEgc2lnbmF0dXJl"),
    }
    for name, response in cases.items():
        assert response.status_code == 401, name
    assert _state(engine) == before
    assert before[3] == ["pending_payment"]


def test_signature_is_checked_before_parsing(db: Session) -> None:
    response = deliver(b"{not json", key="wrong")
    assert response.status_code == 401


def test_malformed_json_with_valid_signature_is_400() -> None:
    assert deliver(b"{not json").status_code == 400
