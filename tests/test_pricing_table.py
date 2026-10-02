import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pricing import PricingError, quote

PRICED = [
    # (id, package, kids, addons, total_cents)
    ("standard at 15 included kids", "standard", 15, {}, 39900),
    ("standard at 16 adds one kid", "standard", 16, {}, 43400),
    ("plus at 20 included kids", "plus", 20, {}, 54900),
    ("plus at 21 adds one kid", "plus", 21, {}, 58400),
    ("premium at 25 included kids", "premium", 25, {}, 74900),
    ("premium at 26 adds one kid", "premium", 26, {}, 78400),
    ("minimum headcount", "standard", 1, {}, 39900),
    ("hard cap headcount", "standard", 30, {}, 39900 + 15 * 3500),
    ("demo script: standard, 18 kids, one pizza pack", "standard", 18, {"pizza_pack": 1}, 54400),
    ("standard pays for vr, ddr, decorations", "standard", 10, {"vr": 1, "ddr": 1, "decorations": 1}, 39900 + 6000 + 4000 + 3000),
    ("plus gets decorations free", "plus", 10, {"decorations": 1}, 54900),
    ("plus still pays for vr and ddr", "plus", 10, {"vr": 1, "ddr": 1}, 54900 + 6000 + 4000),
    ("premium gets vr, ddr, decorations free", "premium", 10, {"vr": 1, "ddr": 1, "decorations": 1}, 74900),
    ("pizza packs multiply", "standard", 10, {"pizza_pack": 3}, 39900 + 3 * 4000),
    ("drinks packs multiply", "standard", 10, {"drinks_pack": 2}, 39900 + 2 * 2000),
    ("zero quantity is ignored", "standard", 10, {"pizza_pack": 0, "vr": 0}, 39900),
    ("tournament alone", "standard", 10, {"tournament": 1}, 39900 + 19900),
    ("tournament with highlight reel", "standard", 10, {"tournament": 1, "highlight_reel": 1}, 39900 + 19900 + 5000),
    ("extra hour", "premium", 25, {"extra_hour": 1}, 74900 + 7500),
]

REJECTED = [
    # (id, package, kids, addons, message fragment)
    ("highlight reel without tournament", "standard", 10, {"highlight_reel": 1}, "requires Tournament"),
    ("highlight reel with zero tournaments", "standard", 10, {"highlight_reel": 1, "tournament": 0}, "requires Tournament"),
    ("zero kids", "standard", 0, {}, "between 1 and 30"),
    ("over the hard cap", "premium", 31, {}, "between 1 and 30"),
    ("unknown package", "venue_buyout", 10, {}, "Unknown package"),
    ("unknown add-on", "standard", 10, {"cake": 1}, "Unknown add-on"),
    ("extra hour is on/off", "standard", 10, {"extra_hour": 2}, "between 0 and 1"),
    ("negative quantity", "standard", 10, {"pizza_pack": -1}, "between 0 and 10"),
    ("too many packs", "standard", 10, {"drinks_pack": 11}, "between 0 and 10"),
]


@pytest.mark.parametrize(("package", "kids", "addons", "total"), [case[1:] for case in PRICED], ids=[case[0] for case in PRICED])
def test_pricing_table(package: str, kids: int, addons: dict[str, int], total: int) -> None:
    result = quote(package, kids, addons)
    assert result.total_cents == total
    assert result.deposit_cents == 10000
    assert result.balance_cents == total - 10000
    assert sum(item.amount_cents for item in result.line_items) == total


@pytest.mark.parametrize(("package", "kids", "addons", "message"), [case[1:] for case in REJECTED], ids=[case[0] for case in REJECTED])
def test_pricing_rejects(package: str, kids: int, addons: dict[str, int], message: str) -> None:
    with pytest.raises(PricingError, match=message):
        quote(package, kids, addons)


@pytest.mark.parametrize(
    ("package", "included"),
    [("standard", set()), ("plus", {"decorations"}), ("premium", {"vr", "ddr", "decorations"})],
)
def test_included_addons_show_as_included_at_zero(package: str, included: set[str]) -> None:
    result = quote(package, 10, {})
    shown = {item.key for item in result.line_items if item.included}
    assert shown == included
    assert all(item.amount_cents == 0 for item in result.line_items if item.included)


def test_selecting_an_included_addon_does_not_duplicate_it() -> None:
    result = quote("premium", 10, {"vr": 1})
    assert [item.key for item in result.line_items].count("vr") == 1


def test_extra_hour_extends_displayed_duration() -> None:
    assert quote("standard", 10, {}).hours == 3
    assert quote("standard", 10, {"extra_hour": 1}).hours == 4


def test_quote_endpoint_matches_pricing_function() -> None:
    client = TestClient(app)
    response = client.post("/api/quote", json={"package": "standard", "kids": 18, "addons": {"pizza_pack": 1}})
    assert response.status_code == 200
    body = response.json()
    assert body["total_cents"] == 54400
    assert body["balance_cents"] == 44400
    assert [item["key"] for item in body["line_items"]] == ["package", "additional_kids", "pizza_pack"]


def test_quote_endpoint_rejects_invalid_combination() -> None:
    client = TestClient(app)
    response = client.post("/api/quote", json={"package": "standard", "kids": 10, "addons": {"highlight_reel": 1}})
    assert response.status_code == 422
    assert "requires Tournament" in response.json()["detail"]


def test_catalog_mirrors_pricing_tables() -> None:
    body = TestClient(app).get("/api/catalog").json()
    assert [(p["key"], p["price_cents"], p["included_kids"]) for p in body["packages"]] == [
        ("standard", 39900, 15),
        ("plus", 54900, 20),
        ("premium", 74900, 25),
    ]
    assert {a["key"]: a["price_cents"] for a in body["addons"]}["highlight_reel"] == 5000
    assert body["deposit_cents"] == 10000


def _tax(monkeypatch: pytest.MonkeyPatch, mode: str, percent: str) -> None:
    from decimal import Decimal

    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "tax_mode", mode)
    monkeypatch.setattr(settings, "tax_rate_percent", Decimal(percent))


def test_included_tax_adds_no_line(monkeypatch: pytest.MonkeyPatch) -> None:
    _tax(monkeypatch, "included", "8.375")  # a rate set but mode "included": prices already contain it
    result = quote("plus", 20, {"pizza_pack": 1})
    assert [item.key for item in result.line_items] == ["package", "decorations", "pizza_pack"]
    assert result.total_cents == 58900


def test_line_item_tax_is_added_to_the_total(monkeypatch: pytest.MonkeyPatch) -> None:
    _tax(monkeypatch, "line_item", "8.375")
    result = quote("plus", 20, {"pizza_pack": 1})
    tax = [item for item in result.line_items if item.key == "sales_tax"]
    assert len(tax) == 1
    assert tax[0].label == "Sales tax"
    # 58900 * 8.375% = 4932.875 -> 4933 rounded half up
    assert tax[0].amount_cents == 4933
    assert result.total_cents == 58900 + 4933
    assert result.balance_cents == result.total_cents - result.deposit_cents


def test_tax_rounds_half_up_and_never_uses_floats(monkeypatch: pytest.MonkeyPatch) -> None:
    _tax(monkeypatch, "line_item", "50")  # exactly half of 39900, no rounding at all
    assert [i.amount_cents for i in quote("standard", 15, {}).line_items if i.key == "sales_tax"] == [19950]
    _tax(monkeypatch, "line_item", "0.01")  # 39900 * 0.0001 = 3.99 -> 4, rounded half up
    assert [i.amount_cents for i in quote("standard", 15, {}).line_items if i.key == "sales_tax"] == [4]
    _tax(monkeypatch, "line_item", "1.25")  # 39900 * 0.0125 = 498.75 -> 499, the half-up case
    assert [i.amount_cents for i in quote("standard", 15, {}).line_items if i.key == "sales_tax"] == [499]


def test_tax_lands_in_the_snapshot_and_the_booking(monkeypatch: pytest.MonkeyPatch, db) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.models import Booking
    from tests.helpers import checkout, client_from, deliver, make_slot, next_saturday, payment_event, start_hold

    _tax(monkeypatch, "line_item", "8.375")
    funnel = client_from("203.0.113.190")
    hold_id = start_hold(funnel, make_slot(db, next_saturday(7)).id).json()["hold_id"]
    paid = checkout(funnel, hold_id, 1)
    assert paid.status_code == 200
    keys = [item["key"] for item in paid.json()["party"]["line_items"]]
    assert "sales_tax" in keys, "checkout prices with tax, so the snapshot carries it"
    deliver(payment_event(hold_id))
    booking = db.scalars(select(Booking)).one()
    assert [item["key"] for item in booking.line_items][-1] == "sales_tax"
    assert booking.total_cents == sum(item["amount_cents"] for item in booking.line_items)
