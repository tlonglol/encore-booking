from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.pricing import (
    ADDITIONAL_KID_CENTS,
    ADDONS,
    DEPOSIT_CENTS,
    MAX_KIDS,
    MAX_PACK_QUANTITY,
    MIN_KIDS,
    PACKAGES,
    PricingError,
    quote,
)
from app.schemas import LineItemOut, QuoteOut, QuoteRequest

router = APIRouter()


@router.post("/api/quote")
def post_quote(request: QuoteRequest) -> QuoteOut:
    try:
        result = quote(request.package, request.kids, request.addons)
    except PricingError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return QuoteOut(
        package=result.package,
        kids=result.kids,
        addons=result.addons,
        hours=result.hours,
        line_items=[LineItemOut(**asdict(item)) for item in result.line_items],
        total_cents=result.total_cents,
        deposit_cents=result.deposit_cents,
        balance_cents=result.balance_cents,
    )


@router.get("/api/catalog")
def catalog() -> dict[str, object]:
    """Display data for the booking page, straight from the pricing tables. Totals still come from /api/quote."""
    return {
        "packages": [
            {
                "key": p.key,
                "label": p.label,
                "price_cents": p.price_cents,
                "included_kids": p.included_kids,
                "hours": p.hours,
                "included_addons": sorted(p.included_addons),
            }
            for p in PACKAGES.values()
        ],
        "addons": [
            {"key": a.key, "label": a.label, "price_cents": a.price_cents, "has_quantity": a.has_quantity, "requires": a.requires}
            for a in ADDONS.values()
        ],
        "additional_kid_cents": ADDITIONAL_KID_CENTS,
        "deposit_cents": DEPOSIT_CENTS,
        "min_kids": MIN_KIDS,
        "max_kids": MAX_KIDS,
        "max_pack_quantity": MAX_PACK_QUANTITY,
    }
