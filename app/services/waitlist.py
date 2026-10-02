from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Slot, WaitlistEntry
from app.services.holds import normalize_phone

# A signup costs nothing to make and is only worth having if a person made it, so the same gentle shape
# the hold caps use: a rolling hour per address, generous enough that a family on one phone network
# never notices.
SIGNUPS_PER_HOUR = 10


class SlotUnknown(Exception):
    """No such date. Answered the same as a malformed one, so probing the endpoint teaches nothing."""


class TooManySignups(Exception):
    pass


@dataclass(frozen=True)
class WaitlistInput:
    slot_id: int
    email: str
    phone: str | None
    client_ip: str | None


def _enforce_rate(db: Session, client_ip: str | None, per_hour: int) -> None:
    if client_ip is None:
        return
    recent = db.execute(
        text(
            "SELECT count(*) FROM waitlist"
            " WHERE client_ip = CAST(:ip AS inet) AND created_at > now() - interval '1 hour'"
        ),
        {"ip": client_ip},
    ).scalar_one()
    if recent >= per_hour:
        raise TooManySignups()


def join_waitlist(db: Session, data: WaitlistInput, per_hour: int = SIGNUPS_PER_HOUR) -> Slot:
    """Records one parent waiting on one date.

    Idempotent on (email, slot): asking twice is the same ask, and the caller answers both the same way
    so a prober cannot tell a fresh signup from a repeat. Never checks whether the date is currently
    booked: a date can lapse back to open between the page rendering and the submit, and refusing then
    would be refusing the exact person this exists for.
    """
    slot = db.get(Slot, data.slot_id)
    if slot is None:
        raise SlotUnknown()

    phone = normalize_phone(data.phone) if data.phone and data.phone.strip() else None
    _enforce_rate(db, data.client_ip, per_hour)
    db.execute(
        insert(WaitlistEntry)
        .values(
            slot_id=slot.id,
            email=data.email.strip().lower(),
            phone=phone,
            client_ip=data.client_ip,
        )
        .on_conflict_do_nothing(constraint="waitlist_one_per_email_per_slot")
    )
    db.commit()
    return slot
