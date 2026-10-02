from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.notifications import notify_new_request
from app.pricing import PACKAGES, PricingError
from app.routes.holds import client_ip
from app.schemas import RequestCreate, RequestOut
from app.services.requests import DuplicateRequest, RequestInput, RequestNotAllowed, create_request
from app.timeutil import format_clock, format_date, venue_today

router = APIRouter()


def require_requests_enabled() -> None:
    """The funnel takes no requests in v1, and a closed door that still answers is a door. As a dependency
    rather than a check in the body, so a malformed request is refused before it is ever validated: a 422
    would tell a prober the endpoint exists and what shape it wants. The table, the service and the admin
    list all stay, so reopening this is one environment variable."""
    if not get_settings().requests_enabled:
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/api/requests", status_code=201, dependencies=[Depends(require_requests_enabled)])
def post_request(body: RequestCreate, request: Request, db: Session = Depends(get_db)) -> RequestOut:
    settings = get_settings()
    data = RequestInput(
        local_date=body.date,
        preferred_time=body.preferred_time.replace(second=0, microsecond=0),
        package=body.package,
        kids=body.kids,
        addons=body.addons,
        first_name=body.first_name,
        last_name=body.last_name,
        phone=body.phone,
        email=str(body.email),
        kid_name=body.kid_name,
        kid_age=body.kid_age,
        birthday=body.birthday,
        notes=body.notes,
        client_ip=client_ip(request),
    )
    try:
        created = create_request(
            db, data, venue_today(datetime.now(UTC)), settings.min_lead_days, settings.weekend_default
        )
    except (PricingError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RequestNotAllowed as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DuplicateRequest as error:
        raise HTTPException(
            status_code=409,
            detail="We already have a request for that date from this phone or email. We'll text you back soon.",
        ) from error
    notify_new_request(db, settings, created.id)
    return RequestOut(
        request_id=created.id,
        day_label=format_date(created.local_date),
        time_label=format_clock(created.preferred_time),
        package_label=PACKAGES[created.package].label,
        kids=created.kids,
        total_cents=created.total_cents,
    )
