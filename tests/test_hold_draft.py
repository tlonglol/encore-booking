import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Hold
from tests.conftest import FakeSquareApi
from tests.helpers import client, client_from, make_hold, make_slot, next_saturday, start_hold


def draft(test_client, hold_id: str, **overrides: object):  # type: ignore[no-untyped-def]
    body: dict[str, object] = {"package": "standard", "kids": 18, "addons": {"pizza_pack": 1}}
    body.update(overrides)
    return test_client.patch(f"/api/holds/{hold_id}/draft", json=body)


def test_draft_prices_on_the_server_and_ignores_client_prices(db: Session, square_api: FakeSquareApi) -> None:
    funnel = client_from("192.0.2.40")
    hold_id = start_hold(funnel, make_slot(db, next_saturday(7)).id).json()["hold_id"]
    response = draft(funnel, hold_id, total_cents=1, deposit_cents=1)
    assert response.status_code == 200
    body = response.json()
    assert body["total_cents"] == 54400
    assert body["deposit_cents"] == 10000
    assert body["balance_cents"] == 44400
    hold = db.scalar(select(Hold).where(Hold.id == uuid.UUID(hold_id)))
    assert hold is not None
    assert hold.total_cents == 54400
    assert [item["key"] for item in hold.line_items] == ["package", "additional_kids", "pizza_pack"]
    assert hold.square_order_id is None and hold.checkout_url is None, "a draft never starts checkout"
    assert hold.phone is None and hold.email is None, "a draft never writes contact details"
    assert square_api.requests == []


def test_draft_can_be_rewritten_as_the_parent_changes_their_mind(db: Session) -> None:
    funnel = client_from("192.0.2.41")
    hold_id = start_hold(funnel, make_slot(db, next_saturday(7)).id).json()["hold_id"]
    assert draft(funnel, hold_id).json()["total_cents"] == 54400
    second = draft(funnel, hold_id, package="premium", kids=25, addons={})
    assert second.status_code == 200
    assert second.json()["total_cents"] == 74900
    assert [item["key"] for item in second.json()["line_items"]] == ["package", "vr", "ddr", "decorations"]


def test_draft_validates_like_checkout(db: Session) -> None:
    funnel = client_from("192.0.2.42")
    hold_id = start_hold(funnel, make_slot(db, next_saturday(7)).id).json()["hold_id"]
    assert draft(funnel, hold_id, kids=31).status_code == 422
    assert draft(funnel, hold_id, package="deluxe").status_code == 422
    assert draft(funnel, hold_id, addons={"highlight_reel": 1}).status_code == 422
    assert draft(funnel, str(uuid.uuid4())).status_code == 404


def test_draft_on_an_expired_hold_is_refused(db: Session) -> None:
    hold = make_hold(db, make_slot(db, next_saturday(7)), expires_in=timedelta(minutes=-1))
    response = draft(client(), str(hold.id))
    assert response.status_code == 409
    assert response.json()["status"] == "expired"


def test_draft_does_not_create_a_booking(db: Session, engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    funnel = client_from("192.0.2.43")
    hold_id = start_hold(funnel, make_slot(db, next_saturday(7)).id).json()["hold_id"]
    draft(funnel, hold_id)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bookings")).scalar_one() == 0
        assert conn.execute(text("SELECT status FROM holds WHERE id = :id"), {"id": hold_id}).scalar_one() == "pending_payment"
