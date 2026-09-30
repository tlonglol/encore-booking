from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.services.admin_auth import COOKIE_NAME, SESSION_SECONDS, issue_token, password_matches, token_valid
from app.services.slots import SlotConflict, set_blocked

router = APIRouter(prefix="/api/admin")


class LoginIn(BaseModel):
    password: str


def require_admin(request: Request) -> None:
    if not token_valid(request.cookies.get(COOKIE_NAME), get_settings().session_secret, datetime.now(UTC)):
        raise HTTPException(status_code=401, detail="Admin login required")


@router.post("/login")
def login(body: LoginIn, response: Response) -> dict[str, bool]:
    settings = get_settings()
    if not settings.admin_password or not settings.session_secret:
        raise HTTPException(status_code=503, detail="Admin login is not configured")
    if not password_matches(body.password, settings.admin_password):
        raise HTTPException(status_code=401, detail="Wrong password")
    response.set_cookie(
        COOKIE_NAME,
        issue_token(settings.session_secret, datetime.now(UTC)),
        max_age=SESSION_SECONDS,
        path="/api/admin",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME, path="/api/admin", secure=True, httponly=True, samesite="lax")
    return {"ok": True}


@router.get("/session", dependencies=[Depends(require_admin)])
def session_check() -> dict[str, bool]:
    return {"ok": True}


def _set_blocked(slot_id: int, blocked: bool, db: Session) -> dict[str, object]:
    try:
        slot = set_blocked(db, slot_id, blocked)
    except SlotConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot not found")
    return {"id": slot.id, "blocked": slot.blocked_at is not None}


@router.post("/slots/{slot_id}/block", dependencies=[Depends(require_admin)])
def block_slot(slot_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    return _set_blocked(slot_id, True, db)


@router.post("/slots/{slot_id}/unblock", dependencies=[Depends(require_admin)])
def unblock_slot(slot_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    return _set_blocked(slot_id, False, db)
