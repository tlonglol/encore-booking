from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.config import get_settings

DEPOSIT_CENTS = 10000
ADDITIONAL_KID_CENTS = 3500
MIN_KIDS = 1
MAX_KIDS = 30
MAX_PACK_QUANTITY = 10


class PricingError(ValueError):
    pass


@dataclass(frozen=True)
class Package:
    key: str
    label: str
    price_cents: int
    included_kids: int
    hours: int
    included_addons: frozenset[str]


@dataclass(frozen=True)
class Addon:
    key: str
    label: str
    price_cents: int
    has_quantity: bool
    requires: str | None = None


PACKAGES: dict[str, Package] = {
    "standard": Package("standard", "Standard", 39900, 15, 3, frozenset()),
    "plus": Package("plus", "Plus", 54900, 20, 3, frozenset({"decorations"})),
    "premium": Package("premium", "Premium", 74900, 25, 4, frozenset({"vr", "ddr", "decorations"})),
}

ADDONS: dict[str, Addon] = {
    "tournament": Addon("tournament", "Tournament + Livestream", 19900, False),
    "highlight_reel": Addon("highlight_reel", "Highlight Reel", 5000, False, requires="tournament"),
    "extra_hour": Addon("extra_hour", "Extra Hour", 7500, False),
    "vr": Addon("vr", "VR Gaming Session", 6000, False),
    "ddr": Addon("ddr", "DDR / Stepmania Pad", 4000, False),
    "decorations": Addon("decorations", "Gamer Decorations", 3000, False),
    "pizza_pack": Addon("pizza_pack", "Pizza Pack", 4000, True),
    "drinks_pack": Addon("drinks_pack", "Drinks Pack", 2000, True),
}


@dataclass(frozen=True)
class LineItem:
    key: str
    label: str
    quantity: int
    unit_cents: int
    amount_cents: int
    included: bool = False


@dataclass(frozen=True)
class Quote:
    package: str
    kids: int
    addons: dict[str, int]
    hours: int
    line_items: list[LineItem]
    total_cents: int
    deposit_cents: int
    balance_cents: int

    def line_items_json(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.line_items]


def _normalize_addons(requested: dict[str, int]) -> dict[str, int]:
    selected: dict[str, int] = {}
    for key, quantity in requested.items():
        addon = ADDONS.get(key)
        if addon is None:
            raise PricingError(f"Unknown add-on: {key}")
        max_quantity = MAX_PACK_QUANTITY if addon.has_quantity else 1
        if not 0 <= quantity <= max_quantity:
            raise PricingError(f"{addon.label} quantity must be between 0 and {max_quantity}")
        if quantity:
            selected[key] = quantity
    for key in selected:
        required = ADDONS[key].requires
        if required and required not in selected:
            raise PricingError(f"{ADDONS[key].label} requires {ADDONS[required].label}")
    return selected


def tax_rate() -> Decimal:
    """The sales tax percent to add as its own line. Zero when the prices already include it."""
    settings = get_settings()
    return settings.tax_rate_percent if settings.tax_mode == "line_item" else Decimal("0")


def tax_on(taxable_cents: int, rate_percent: Decimal) -> int:
    """Exact decimal arithmetic, rounded half up. Money never passes through a float."""
    return int((Decimal(taxable_cents) * rate_percent / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def quote(package_key: str, kids: int, addons: dict[str, int]) -> Quote:
    """The only place prices are computed. Used by /api/quote, drafts, checkout and requests, so the tax
    setting is read here rather than threaded through every caller, where one could drift from the rest."""
    package = PACKAGES.get(package_key)
    if package is None:
        raise PricingError(f"Unknown package: {package_key}")
    if not MIN_KIDS <= kids <= MAX_KIDS:
        raise PricingError(f"Kids must be between {MIN_KIDS} and {MAX_KIDS}")
    selected = _normalize_addons(addons)

    items = [
        LineItem(
            "package",
            f"{package.label} package, up to {package.included_kids} kids, {package.hours} hours",
            1,
            package.price_cents,
            package.price_cents,
        )
    ]
    extra_kids = max(0, kids - package.included_kids)
    if extra_kids:
        items.append(
            LineItem("additional_kids", "Additional kids", extra_kids, ADDITIONAL_KID_CENTS, extra_kids * ADDITIONAL_KID_CENTS)
        )
    for key, addon in ADDONS.items():
        if key in package.included_addons:
            items.append(LineItem(key, addon.label, 1, 0, 0, included=True))
        elif key in selected:
            quantity = selected[key]
            items.append(LineItem(key, addon.label, quantity, addon.price_cents, quantity * addon.price_cents))

    rate = tax_rate()
    if rate > 0:
        tax = tax_on(sum(item.amount_cents for item in items), rate)
        items.append(LineItem("sales_tax", "Sales tax", 1, tax, tax))

    total = sum(item.amount_cents for item in items)
    hours = package.hours + (1 if "extra_hour" in selected else 0)
    return Quote(
        package=package.key,
        kids=kids,
        addons=selected,
        hours=hours,
        line_items=items,
        total_cents=total,
        deposit_cents=DEPOSIT_CENTS,
        balance_cents=total - DEPOSIT_CENTS,
    )
