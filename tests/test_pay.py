"""The deposit, taken on our own page.

The card never reaches this server: the Web Payments SDK collects it in Square-hosted iframes and hands
back a single-use token, and that token is the only thing the client sends. These pin the four ways this
can go wrong with someone's money: charging twice for one party, charging once and recording nothing,
refusing a parent who simply pressed the button twice, and locking out a parent whose first card was
declined.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tests.conftest import FakeSquareApi
from tests.helpers import (
    checkout,
    client,
    client_from,
    create_hold_via_api,
    deliver,
    make_hold,
    make_slot,
    next_saturday,
    payment_event,
    start_hold,
)


def pay(test_client, hold_id: str, token: str = "cnon:test-token"):  # type: ignore[no-untyped-def]
    return test_client.post(f"/api/holds/{hold_id}/pay", json={"token": token})


def counts(engine: Engine) -> dict[str, int]:
    with engine.begin() as conn:
        return {
            table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in ("bookings", "notifications", "payment_flags")
        }


# ---- 21. the endpoint itself ----


def test_a_card_token_books_the_party_and_returns_the_code(db: Session, square_api: FakeSquareApi) -> None:
    hold_id, _ = create_hold_via_api(db)
    response = pay(client(), hold_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["confirmation_code"]) == 10
    assert body["manage_token"]
    assert body["party"]["confirmation_code"] == body["confirmation_code"]

    assert client().get(f"/api/holds/{hold_id}").json()["status"] == "booked"
    charge = next(r for r in square_api.requests if r["path"] == "/v2/payments")
    assert charge["body"]["amount_money"] == {"amount": 10000, "currency": "USD"}
    assert charge["body"]["reference_id"] == hold_id, "the webhook backstop finds the hold by this"


def test_the_client_cannot_influence_what_is_charged(db: Session, square_api: FakeSquareApi) -> None:
    """The request body carries a token and nothing else. There is no amount to tamper with, and the
    deposit charged is the hold's own server-set figure."""
    hold_id, _ = create_hold_via_api(db)
    response = client().post(
        f"/api/holds/{hold_id}/pay",
        json={"token": "cnon:test-token", "amount_cents": 1, "deposit_cents": 1, "total_cents": 1},
    )
    assert response.status_code == 200, response.text
    charge = next(r for r in square_api.requests if r["path"] == "/v2/payments")
    assert charge["body"]["amount_money"]["amount"] == 10000


def test_an_expired_hold_is_refused_and_nothing_is_charged(db: Session, square_api: FakeSquareApi) -> None:
    hold = make_hold(db, make_slot(db, next_saturday(7)), expires_in=timedelta(minutes=-1))
    response = pay(client(), str(hold.id))
    assert response.status_code == 409
    assert response.json()["status"] == "expired"
    assert [r for r in square_api.requests if r["path"] == "/v2/payments"] == []


def test_a_hold_without_a_snapshot_is_refused(db: Session, square_api: FakeSquareApi) -> None:
    """Paying before the Info step has priced the party would charge a deposit against nothing."""
    slot = make_slot(db, next_saturday(7))
    funnel = client_from("198.51.100.70")
    hold_id = start_hold(funnel, slot.id).json()["hold_id"]
    response = pay(funnel, hold_id)
    assert response.status_code == 409
    assert [r for r in square_api.requests if r["path"] == "/v2/payments"] == []


def test_a_declined_card_keeps_the_hold_and_the_date(db: Session, square_api: FakeSquareApi) -> None:
    hold_id, _ = create_hold_via_api(db)
    square_api.decline_code = "INSUFFICIENT_FUNDS"
    response = pay(client(), hold_id, token="cnon:declined")
    assert response.status_code == 402
    assert "insufficient funds" in response.json()["detail"].lower()
    assert client().get(f"/api/holds/{hold_id}").json()["status"] == "pending_payment", "the date is still theirs"


def test_square_being_unreachable_charges_nothing_and_keeps_the_hold(
    db: Session, square_api: FakeSquareApi
) -> None:
    hold_id, _ = create_hold_via_api(db)
    square_api.fail_with = 500
    response = pay(client(), hold_id)
    assert response.status_code == 502
    assert client().get(f"/api/holds/{hold_id}").json()["status"] == "pending_payment"


def test_a_payment_that_is_not_completed_books_nothing(db: Session, square_api: FakeSquareApi) -> None:
    """APPROVED but not captured is not money in the account, so it is not a party."""
    hold_id, _ = create_hold_via_api(db)
    square_api.payment_status = "APPROVED"
    response = pay(client(), hold_id)
    assert response.status_code == 502
    assert client().get(f"/api/holds/{hold_id}").json()["status"] == "pending_payment"


# ---- 22. the parent pressed the button twice ----


def test_pressing_pay_twice_books_once_and_answers_the_same(db: Session, engine: Engine) -> None:
    hold_id, _ = create_hold_via_api(db)
    first = pay(client(), hold_id)
    second = pay(client(), hold_id)
    assert first.status_code == 200
    assert second.status_code == 200, "a double click is not an error the parent should see"
    assert second.json()["confirmation_code"] == first.json()["confirmation_code"]
    assert counts(engine)["bookings"] == 1


def test_two_simultaneous_pay_requests_charge_once(db: Session, engine: Engine, square_api: FakeSquareApi) -> None:
    """Two in-flight at once, the way a double tap on a slow phone actually arrives."""
    hold_id, _ = create_hold_via_api(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [f.result() for f in [pool.submit(pay, client(), hold_id) for _ in range(2)]]

    assert [r.status_code for r in responses] == [200, 200]
    assert len({r.json()["confirmation_code"] for r in responses}) == 1, "one party, one code"
    after = counts(engine)
    assert after["bookings"] == 1
    assert after["payment_flags"] == 0, "nothing for the owner to clean up"
    assert len(square_api.payments) == 1, "one payment at Square, because one token means one key"


def test_one_parent_is_notified_once_however_many_times_they_press(db: Session, engine: Engine) -> None:
    hold_id, _ = create_hold_via_api(db)
    pay(client(), hold_id)
    first = counts(engine)["notifications"]
    pay(client(), hold_id)
    pay(client(), hold_id)
    assert counts(engine)["notifications"] == first


# ---- 23. tokens ----


def test_a_retry_after_a_decline_succeeds_with_a_fresh_token(db: Session, square_api: FakeSquareApi) -> None:
    """The reason the idempotency key is derived from the token and not from the hold. Keyed on the hold,
    Square would replay the decline forever and the parent could never pay for their own party."""
    hold_id, _ = create_hold_via_api(db)
    square_api.decline_code = "CARD_DECLINED"
    assert pay(client(), hold_id, token="cnon:first-card").status_code == 402

    square_api.decline_code = None
    retry = pay(client(), hold_id, token="cnon:second-card")
    assert retry.status_code == 200, retry.text
    assert client().get(f"/api/holds/{hold_id}").json()["status"] == "booked"


def test_a_spent_token_cannot_be_replayed_onto_another_hold(db: Session, engine: Engine) -> None:
    """Square rejects a reused card token, and that rejection must not book the second party."""
    first_hold, _ = create_hold_via_api(db, days_ahead=7, n=1)
    second_hold, _ = create_hold_via_api(db, days_ahead=14, n=2)
    assert pay(client(), first_hold, token="cnon:one-use").status_code == 200

    replay = pay(client(), second_hold, token="cnon:one-use")
    assert replay.status_code in (402, 502), replay.text
    assert client().get(f"/api/holds/{second_hold}").json()["status"] == "pending_payment"
    assert counts(engine)["bookings"] == 1


# ---- 24. the webhook backstop, in both orders ----


def test_the_webhook_for_an_already_committed_payment_changes_nothing(
    db: Session, engine: Engine, square_api: FakeSquareApi
) -> None:
    hold_id, _ = create_hold_via_api(db)
    assert pay(client(), hold_id).status_code == 200
    before = counts(engine)

    payment_id = next(iter(square_api.payments.values()))["id"]
    response = deliver(payment_event(hold_id, payment_id=str(payment_id)))
    assert response.json() == {"outcome": "duplicate"}
    assert counts(engine) == before, "no second booking, no second notification, no flag"


def test_a_webhook_that_wins_the_race_is_the_booking_the_parent_is_shown(
    db: Session, engine: Engine, square_api: FakeSquareApi
) -> None:
    """The backstop's whole reason for existing: Square charged the card and our process died before
    committing. The webhook commits, and the parent's retry must find that booking rather than make a
    second one or be told their money vanished."""
    hold_id, _ = create_hold_via_api(db)
    # The webhook lands first, carrying the reference_id the pay endpoint would have sent.
    assert deliver(payment_event(hold_id, payment_id="pay-webhook-first")).json() == {"outcome": "booked"}
    booked_code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]

    retry = pay(client(), hold_id)
    assert retry.status_code == 200, "they paid; they must not be told their date was released"
    assert retry.json()["confirmation_code"] == booked_code, "and it is the booking that already exists"
    assert [r for r in square_api.requests if r["path"] == "/v2/payments"] == [], "and no second charge"
    assert counts(engine)["bookings"] == 1


def test_a_second_distinct_payment_on_a_booked_hold_is_flagged_not_swallowed(
    db: Session, engine: Engine
) -> None:
    """Two genuinely different payments for one party is real money the owner has to give back, so it
    becomes a flag rather than a silent duplicate."""
    hold_id, _ = create_hold_via_api(db)
    assert pay(client(), hold_id).status_code == 200
    deliver(payment_event(hold_id, payment_id="pay-a-second-card"))
    after = counts(engine)
    assert after["bookings"] == 1
    assert after["payment_flags"] == 1
