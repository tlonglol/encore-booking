from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.routes.holds import client_ip
from app.schemas import WaitlistIn
from app.services.waitlist import SlotUnknown, TooManySignups, WaitlistInput, join_waitlist
from app.timeutil import format_day

router = APIRouter()


@router.post("/api/waitlist", status_code=201)
def post_waitlist(body: WaitlistIn, request: Request, db: Session = Depends(get_db)) -> Any:
    """Always the same friendly answer: a repeat signup neither errors nor reveals it was already there."""
    try:
        slot = join_waitlist(
            db,
            WaitlistInput(
                slot_id=body.slot_id,
                email=str(body.email),
                phone=body.phone,
                client_ip=client_ip(request),
            ),
        )
    except SlotUnknown as error:
        raise HTTPException(status_code=404, detail="That date isn't on the calendar.") from error
    except ValueError as error:
        # A phone that is not a phone. The email is already validated by the schema.
        raise HTTPException(status_code=422, detail=str(error)) from error
    except TooManySignups as error:
        raise HTTPException(
            status_code=429,
            detail="That's a lot of signups from this network. Give it a few minutes, or call (646) 203-8087.",
        ) from error
    return {"detail": f"We'll text you if {format_day(slot.starts_at)} opens."}
