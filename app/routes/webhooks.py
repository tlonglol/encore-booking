from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.services.booking import handle_square_webhook
from app.services.square import SIGNATURE_HEADER

router = APIRouter()


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/api/webhooks/square")
def square_webhook(request: Request, body: bytes = Depends(raw_body), db: Session = Depends(get_db)) -> JSONResponse:
    result = handle_square_webhook(db, get_settings(), body, request.headers.get(SIGNATURE_HEADER))
    return JSONResponse(status_code=result.status_code, content={"outcome": result.outcome})
