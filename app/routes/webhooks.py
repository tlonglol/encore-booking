from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.notifications import notify_after_commit, notify_refund_settled
from app.services.booking import handle_square_webhook
from app.services.square import SIGNATURE_HEADER

router = APIRouter()


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/api/webhooks/square")
def square_webhook(request: Request, body: bytes = Depends(raw_body), db: Session = Depends(get_db)) -> JSONResponse:
    settings = get_settings()
    result = handle_square_webhook(db, settings, body, request.headers.get(SIGNATURE_HEADER))
    notify_after_commit(db, settings, result)
    if result.refund_booking_id is not None:
        notify_refund_settled(db, settings, result.refund_booking_id)
    return JSONResponse(status_code=result.status_code, content={"outcome": result.outcome})
