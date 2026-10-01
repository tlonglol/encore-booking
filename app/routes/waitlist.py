from fastapi import APIRouter, Depends, Request
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import WaitlistEntry
from app.routes.holds import client_ip
from app.schemas import WaitlistIn

router = APIRouter()


@router.post("/api/waitlist", status_code=201)
def post_waitlist(body: WaitlistIn, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    """Always the same friendly answer: a duplicate signup neither errors nor reveals it was already there."""
    db.execute(
        insert(WaitlistEntry)
        .values(email=str(body.email).strip().lower(), client_ip=client_ip(request))
        .on_conflict_do_nothing(index_elements=["email"])
    )
    db.commit()
    return {"detail": "You're on the list. We'll email you when new dates open up."}
