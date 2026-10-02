"""Adding to a confirmed booking: additions only, re-priced by the one pricing function, deposit untouched."""

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Booking
from app.pricing import quote
from tests.conftest import FakeSquareApi
from tests.helpers import client, make_booking, make_hold, make_slot, today


def _booking_on(db: Session, days_away: int = 30) -> Booking:
    """make_hold prices standard / 18 kids / 1 pizza pack, which is what every case below starts from."""
    slot = make_slot(db, today() + timedelta(days=days_away))
    return make_booking(db, make_hold(db, slot), slot)


def _add(booking: Booking, addons: dict[str, int], kids: int):  # type: ignore[no-untyped-def]
    return client().post(
        f"/api/manage/{booking.manage_token}/addons", json={"addons": addons, "kids": kids}
    )


def test_an_addition_raises_only_the_balance(db: Session) -> None:
    booking = _booking_on(db)
    before = (booking.total_cents, booking.deposit_cents)

    response = _add(booking, {"pizza_pack": 1, "drinks_pack": 2}, kids=18)
    assert response.status_code == 200, response.text
    party = response.json()["party"]

    expected = quote("standard", 18, {"pizza_pack": 1, "drinks_pack": 2})
    assert party["total_cents"] == expected.total_cents
    assert party["total_cents"] == before[0] + 2 * 2000
    # The deposit is untouched; the whole increase lands in what is due at the party.
    assert party["deposit_cents"] == before[1]
    assert party["balance_cents"] == expected.total_cents - before[1]


def test_prices_come_from_the_same_quote_function(db: Session) -> None:
    booking = _booking_on(db)
    response = _add(booking, {"pizza_pack": 3, "vr": 1}, kids=22)
    expected = quote("standard", 22, {"pizza_pack": 3, "vr": 1})
    body = response.json()["party"]
    assert body["total_cents"] == expected.total_cents
    assert [item["key"] for item in body["line_items"]] == [item.key for item in expected.line_items]


def test_more_kids_are_priced_as_overage(db: Session) -> None:
    booking = _booking_on(db)
    before = booking.total_cents
    response = _add(booking, {"pizza_pack": 1}, kids=20)
    # Standard includes 15, so 18 to 20 is two more kids beyond an overage that already existed.
    assert response.json()["party"]["total_cents"] == before + 2 * 3500


def test_a_lowered_quantity_is_ignored_not_obeyed(db: Session) -> None:
    """The client's idea of the diff is never trusted: the server keeps the larger of the two."""
    booking = _booking_on(db)
    before = booking.total_cents
    response = _add(booking, {"pizza_pack": 0, "drinks_pack": 1}, kids=18)
    assert response.status_code == 200, response.text
    db.refresh(booking)
    assert booking.addons["pizza_pack"] == 1
    assert booking.total_cents == before + 2000


def test_an_omitted_addon_is_not_removed(db: Session) -> None:
    booking = _booking_on(db)
    _add(booking, {"drinks_pack": 1}, kids=18)
    db.refresh(booking)
    assert booking.addons["pizza_pack"] == 1


def test_lowering_the_headcount_is_refused(db: Session) -> None:
    booking = _booking_on(db)
    response = _add(booking, {"pizza_pack": 2}, kids=12)
    assert response.status_code == 409
    assert "only go up" in response.json()["detail"]
    db.refresh(booking)
    assert booking.kids == 18


def test_a_request_that_changes_nothing_is_refused(db: Session) -> None:
    booking = _booking_on(db)
    response = _add(booking, {"pizza_pack": 1}, kids=18)
    assert response.status_code == 409
    assert "Nothing was added" in response.json()["detail"]


def test_addon_dependencies_still_apply(db: Session) -> None:
    booking = _booking_on(db)
    response = _add(booking, {"highlight_reel": 1}, kids=18)
    assert response.status_code == 422
    assert "requires" in response.json()["detail"]
    db.refresh(booking)
    assert "highlight_reel" not in booking.addons


def test_highlight_reel_is_allowed_alongside_the_tournament(db: Session) -> None:
    booking = _booking_on(db)
    response = _add(booking, {"tournament": 1, "highlight_reel": 1}, kids=18)
    assert response.status_code == 200, response.text
    db.refresh(booking)
    assert booking.addons["highlight_reel"] == 1


def test_an_unknown_addon_is_refused(db: Session) -> None:
    booking = _booking_on(db)
    assert _add(booking, {"pony_rides": 1}, kids=18).status_code == 409


def test_additions_close_before_the_party(db: Session) -> None:
    """Default cutoff is 48 hours: the pizza has to be ordered."""
    booking = _booking_on(db, days_away=1)
    response = _add(booking, {"drinks_pack": 1}, kids=18)
    assert response.status_code == 409
    assert "(646) 203-8087" in response.json()["detail"]
    db.refresh(booking)
    assert "drinks_pack" not in booking.addons


def test_the_manage_page_says_when_additions_close(db: Session) -> None:
    open_booking = client().get(f"/api/manage/{_booking_on(db, days_away=30).manage_token}").json()["can"]
    assert open_booking["can_add_addons"] is True
    assert open_booking["addons_reason"] is None

    closed = client().get(f"/api/manage/{_booking_on(db, days_away=1).manage_token}").json()["can"]
    assert closed["can_add_addons"] is False
    assert "too close" in closed["addons_reason"]


def test_nothing_is_charged_or_refunded(db: Session, square_api: FakeSquareApi) -> None:
    booking = _booking_on(db)
    _add(booking, {"drinks_pack": 2}, kids=18)
    assert square_api.requests == []


def test_a_cancelled_booking_takes_no_additions(db: Session) -> None:
    booking = _booking_on(db)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    assert _add(booking, {"drinks_pack": 1}, kids=18).status_code == 409


def test_the_owner_is_told_what_to_buy_not_the_whole_order(db: Session) -> None:
    booking = _booking_on(db)
    _add(booking, {"pizza_pack": 3, "drinks_pack": 1}, kids=20)
    body = db.execute(text("SELECT body FROM notifications WHERE event = 'addons_changed'")).scalar_one()
    assert "ADDED" in body
    # Two more pizza packs were added, not three; the owner buys the difference.
    assert "Pizza Pack x2" in body
    assert "Drinks Pack x1" in body
    assert "2 more kids (now 20)" in body
    assert "due at the party" in body
