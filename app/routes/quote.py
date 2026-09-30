from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.pricing import PricingError, quote
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
