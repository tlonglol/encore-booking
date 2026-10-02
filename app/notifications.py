import logging
import smtplib
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Booking, Hold, Notification, PartyRequest, Refund, Slot
from app.pricing import ADDONS, PACKAGES
from app.services.booking import WebhookResult
from app.timeutil import format_clock, format_date, format_day, format_time

TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
REQUEST_TIMEOUT_SECONDS = 5.0
VENUE_DETAILS = "538 Main St, 2nd floor, entrance on Division St. Questions: (646) 203-8087."
REFUND_PROMISE = (
    "Cancel or reschedule free up to 7 days before your party. Inside 7 days your deposit isn't refundable,"
    " but you can reschedule once."
)
SMTP_SSL_PORT = 465

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Message:
    audience: str
    channel: str
    recipient: str
    body: str
    subject: str = ""
    # What this announces. Webhook-driven events are claimed once; parent-driven ones are just recorded.
    event: str = "booked"


def dollars(cents: int) -> str:
    return f"${cents // 100:,}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def addons_summary(addons: dict[str, int]) -> str:
    parts = []
    for key, quantity in addons.items():
        addon = ADDONS[key]
        parts.append(f"{addon.label} x{quantity}" if addon.has_quantity else addon.label)
    return ", ".join(parts) or "no add-ons"


def when(starts_at: datetime) -> str:
    return f"{format_day(starts_at)} {format_time(starts_at)}"


def full_name(first: str | None, last: str | None) -> str:
    return " ".join(part for part in (first, last) if part)


def parent_booked_sms(booking: Booking, confirmation_url: str) -> str:
    return (
        f"You're booked! {PACKAGES[booking.package].label} party {when(booking.event_starts_at)} at Encore Esports, "
        f"{booking.kids} kids. {dollars(booking.deposit_cents)} deposit received, "
        f"{dollars(booking.total_cents - booking.deposit_cents)} due at the party. {VENUE_DETAILS} "
        f"Details: {confirmation_url}"
    )


def itemized_lines(line_items: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in line_items:
        quantity = item["quantity"]
        label = f"{item['label']} x{quantity} ({dollars(item['unit_cents'])} each)" if quantity > 1 else item["label"]
        lines.append(f"  {label}: {'Included' if item['included'] else dollars(item['amount_cents'])}")
    return lines


def parent_booked_email_subject(booking: Booking) -> str:
    return f"You're booked: {PACKAGES[booking.package].label} party {format_day(booking.event_starts_at)}"


def parent_booked_email(booking: Booking, confirmation_url: str, manage_url: str) -> str:
    package = PACKAGES[booking.package]
    return "\n".join(
        [
            f"Hi {booking.first_name},",
            "",
            f"You're booked! Your {package.label} party at Encore Esports is confirmed.",
            "",
            f"Confirmation code: {booking.confirmation_code}",
            f"Confirmation page: {confirmation_url}",
            "",
            f"When: {format_day(booking.event_starts_at)} at {format_time(booking.event_starts_at)}",
            f"Package: {package.label}, {booking.kids} kids",
            "",
            "Itemized total:",
            *itemized_lines(booking.line_items),
            f"  Total: {dollars(booking.total_cents)}",
            f"  Deposit received: {dollars(booking.deposit_cents)}",
            f"  Due at the party: {dollars(booking.total_cents - booking.deposit_cents)}",
            "",
            "Where: Encore Esports, 538 Main St, 2nd floor, entrance on Division St.",
            "",
            f"Add extras, move your date or cancel: {manage_url}",
            "",
            REFUND_PROMISE,
            "",
            "Questions? Call (646) 203-8087.",
            "",
        ]
    )


def owner_booked_text(booking: Booking) -> str:
    return (
        f"NEW PARTY: {PACKAGES[booking.package].label} {when(booking.event_starts_at)}, {booking.kids} kids, "
        f"{addons_summary(booking.addons)}. Deposit paid. {full_name(booking.first_name, booking.last_name)} {booking.phone}."
    )


def owner_request_text(request: PartyRequest) -> str:
    return (
        f"PARTY REQUEST: {PACKAGES[request.package].label} {format_date(request.local_date)} at "
        f"{format_clock(request.preferred_time)}, {request.kids} kids, {addons_summary(request.addons)}. "
        f"Total {dollars(request.total_cents)}. "
        f"{full_name(request.first_name, request.last_name)} {request.phone} {request.email}."
    )


def parent_reminder_sms(booking: Booking) -> str:
    """Week-of reminder. Written and tested; nothing sends it yet (needs a scheduler, see SPEC Before going live)."""
    return (
        f"See you {when(booking.event_starts_at)}! {PACKAGES[booking.package].label} party at Encore Esports, "
        f"{booking.kids} kids. Balance {dollars(booking.total_cents - booking.deposit_cents)} due that day. "
        f"{VENUE_DETAILS}"
    )


def parent_review_sms(booking: Booking, review_url: str) -> str:
    """Review request for 1-2 days after the party. Written and tested; nothing sends it yet."""
    return (
        f"Thanks for celebrating {booking.kid_name or 'the birthday'} with us at Encore Esports! "
        f"If you had a great time, a quick review helps us a lot: {review_url}"
    )


def owner_cancelled_text(booking: Booking, refunded: bool, queued: bool, why: str | None = None) -> str:
    if queued:
        deposit = f"{dollars(booking.paid_amount_cents)} refund QUEUED for your approval in admin ({why})"
    elif refunded:
        deposit = f"{dollars(booking.paid_amount_cents)} deposit refunded"
    else:
        deposit = "Deposit NOT refunded, cancelled inside 7 days"
    return (
        f"CANCELLED: {PACKAGES[booking.package].label} {when(booking.event_starts_at)}, "
        f"{full_name(booking.first_name, booking.last_name)} {booking.phone}. {deposit}. Date is open again."
    )


def owner_rescheduled_text(booking: Booking, moved_from: datetime) -> str:
    return (
        f"MOVED: {full_name(booking.first_name, booking.last_name)} {booking.phone} moved their "
        f"{PACKAGES[booking.package].label} party from {format_day(moved_from)} to "
        f"{when(booking.event_starts_at)}. Deposit rides along, nothing charged or refunded."
    )


def owner_addons_text(booking: Booking, added: str) -> str:
    return (
        f"ADDED: {full_name(booking.first_name, booking.last_name)} {booking.phone}, "
        f"{PACKAGES[booking.package].label} {when(booking.event_starts_at)}. Added {added}. "
        f"New total {dollars(booking.total_cents)}, "
        f"{dollars(booking.total_cents - booking.deposit_cents)} due at the party."
    )


def owner_refund_settled_text(booking: Booking, amount_cents: int, status: str) -> str:
    """Square's own word on the refund, which is the one that counts. A failure needs the owner to act."""
    if status == "completed":
        return (
            f"REFUND CONFIRMED: {dollars(amount_cents)} back to "
            f"{full_name(booking.first_name, booking.last_name)} for the {when(booking.event_starts_at)} party. "
            f"Nothing more to do."
        )
    return (
        f"REFUND {status.upper()}: {dollars(amount_cents)} to "
        f"{full_name(booking.first_name, booking.last_name)} ({booking.phone}) did not go through. "
        f"Finish it in Square."
    )


def owner_hold_spike_text(count: int, client_ip: str | None) -> str:
    return (
        f"HEADS UP: {count} dates held in the last hour from one visitor with no payment. "
        f"Nothing is blocked and no date is lost; the holds lapse in 15 minutes each. "
        f"{f'Source {client_ip}. ' if client_ip else ''}Worth a look in admin."
    )


def notify_hold_spike(
    db: Session, settings: Settings, hold_id: uuid.UUID, count: int, client_ip: str | None
) -> None:
    """Best-effort and at most once an hour, so a visitor holding twenty dates produces one message rather
    than ten. Never raises: a hold is not failed over a notification."""
    try:
        recent = db.execute(
            text(
                "SELECT count(*) FROM notifications"
                " WHERE event = 'hold_spike' AND created_at > now() - interval '1 hour'"
            )
        ).scalar_one()
        if recent:
            return
        messages = _owner_messages(settings, owner_hold_spike_text(count, client_ip), event="hold_spike")
    except Exception:
        log.exception("Could not build hold spike notification")
        db.rollback()
        return
    log.warning("Hold spike: %s unpaid holds in the last hour from %s", count, client_ip or "unknown")
    for message in messages:
        try:
            notification_id = _claim(db, message, hold_id=hold_id)
        except Exception:
            log.exception("Could not record hold spike notification")
            db.rollback()
            continue
        if notification_id is None:
            continue
        try:
            status, provider_id = _deliver(settings, message)
            _record(db, notification_id, status, provider_id, None)
        except Exception as error:
            log.warning("owner hold spike notification failed: %s", error)
            db.rollback()
            try:
                _record(db, notification_id, "failed", None, str(error)[:500])
            except Exception:
                log.exception("Could not record notification failure")
                db.rollback()


def owner_late_payment_text(hold: Hold, starts_at: datetime) -> str:
    return (
        f"PAYMENT ON EXPIRED HOLD, call parent: {PACKAGES[hold.package].label} {when(starts_at)}, {hold.kids} kids. "
        f"{dollars(hold.deposit_cents)} received after the hold expired; date NOT confirmed. "
        f"{full_name(hold.first_name, hold.last_name)} {hold.phone}."
    )


def _owner_messages(settings: Settings, body: str, event: str = "booked") -> list[Message]:
    messages = []
    if settings.owner_phone:
        messages.append(Message("owner", "sms", settings.owner_phone, body, event=event))
    if settings.discord_webhook_url:
        messages.append(Message("owner", "discord", "discord", body, event=event))
    return messages


def messages_for(db: Session, settings: Settings, hold_id: uuid.UUID) -> tuple[int | None, list[Message]]:
    """What to announce for this hold's committed state: a live booking, a late payment, or nothing."""
    booking = db.scalar(select(Booking).where(Booking.hold_id == hold_id, Booking.cancelled_at.is_(None)))
    if booking is not None:
        link = f"{settings.frontend_origin}/confirm.html?code={booking.confirmation_code}"
        manage_link = f"{settings.frontend_origin}/manage.html?t={booking.manage_token}"
        parent_sms = Message("parent", "sms", booking.phone, parent_booked_sms(booking, link))
        parent_email = Message(
            "parent",
            "email",
            booking.email,
            parent_booked_email(booking, link, manage_link),
            parent_booked_email_subject(booking),
        )
        return booking.id, [parent_sms, parent_email, *_owner_messages(settings, owner_booked_text(booking))]
    hold = db.get(Hold, hold_id)
    if hold is not None and hold.status == "paid_after_expiry":
        slot = db.get(Slot, hold.slot_id)
        assert slot is not None
        return None, _owner_messages(
            settings, owner_late_payment_text(hold, slot.starts_at), event="late_payment"
        )
    return None, []


def http_client() -> httpx.Client:
    return httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)


def _send_sms(settings: Settings, to: str, body: str) -> str:
    with http_client() as client:
        response = client.post(
            TWILIO_URL.format(sid=settings.twilio_account_sid),
            data={"To": to, "From": settings.twilio_from_number, "Body": body},
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )
    if response.status_code != 201:
        detail = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        raise RuntimeError(f"Twilio {response.status_code} code={detail.get('code')}: {detail.get('message')}")
    return str(response.json().get("sid", ""))


def smtp_client(settings: Settings) -> smtplib.SMTP:
    if settings.smtp_port == SMTP_SSL_PORT:
        return smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=REQUEST_TIMEOUT_SECONDS)
    return smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=REQUEST_TIMEOUT_SECONDS)


def _send_email(settings: Settings, to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=parseaddr(settings.email_from)[1].rpartition("@")[2] or None)
    message.set_content(body)
    with smtp_client(settings) as smtp:
        if settings.smtp_port != SMTP_SSL_PORT:
            smtp.starttls()
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
    return str(message["Message-ID"])


def _send_discord(settings: Settings, body: str) -> str:
    with http_client() as client:
        response = client.post(
            settings.discord_webhook_url,
            params={"wait": "true"},
            json={"content": body[:2000], "allowed_mentions": {"parse": []}},
        )
    if response.status_code != 200:
        raise RuntimeError(f"Discord {response.status_code}")
    return str(response.json().get("id", ""))


def _claim(
    db: Session,
    message: Message,
    hold_id: uuid.UUID | None = None,
    booking_id: int | None = None,
    request_id: int | None = None,
) -> int | None:
    """Writes the row before sending. The unique (subject, audience, channel) key makes a second claim a no-op."""
    statement = insert(Notification).values(
        hold_id=hold_id,
        request_id=request_id,
        booking_id=booking_id,
        audience=message.audience,
        channel=message.channel,
        event=message.event,
        recipient=message.recipient,
        body=message.body,
        status="pending",
    )
    if request_id is not None:
        statement = statement.on_conflict_do_nothing(
            index_elements=["request_id", "audience", "channel"],
            index_where=text("request_id IS NOT NULL"),
        )
    else:
        # The claim index only covers the webhook-driven events, so a cancellation or a move inserts freely:
        # those arrive on a single request that has already decided it is allowed to happen.
        statement = statement.on_conflict_do_nothing(
            index_elements=["hold_id", "audience", "channel"],
            index_where=text("event IN ('booked', 'late_payment', 'refunded')"),
        )
    claimed = db.execute(statement.returning(Notification.id)).scalar_one_or_none()
    db.commit()
    return claimed


def _deliver(settings: Settings, message: Message) -> tuple[str, str | None]:
    """Returns (status, provider message id)."""
    if message.channel == "discord":
        return "sent", _send_discord(settings, message.body)
    if message.channel == "email":
        if not settings.email_enabled:
            log.info("Email not sent (SMTP not configured) to=%s subject=%s body=\n%s", message.recipient, message.subject, message.body)
            return "logged", None
        return "sent", _send_email(settings, message.recipient, message.subject, message.body)
    if not settings.twilio_enabled:
        log.info("SMS not sent (Twilio not configured) to=%s body=%s", message.recipient, message.body)
        return "logged", None
    return "sent", _send_sms(settings, message.recipient, message.body)


def _record(db: Session, notification_id: int, status: str, provider_id: str | None, error: str | None) -> None:
    db.execute(
        text(
            "UPDATE notifications SET status = :status, provider_message_id = :provider_id, error = :error,"
            " sent_at = CASE WHEN :status IN ('sent', 'logged') THEN now() END WHERE id = :id"
        ),
        {"status": status, "provider_id": provider_id, "error": error, "id": notification_id},
    )
    db.commit()


def notify_after_commit(db: Session, settings: Settings, result: WebhookResult) -> None:
    """Best-effort, after commit, never raises. Runs on redeliveries too, so a crash between commit and
    sending is healed by Square's retry; the claim row keeps that from ever sending anything twice."""
    if result.status_code != 200 or result.hold_id is None:
        return
    try:
        booking_id, messages = messages_for(db, settings, result.hold_id)
    except Exception:
        log.exception("Could not build notifications for hold %s", result.hold_id)
        db.rollback()
        return
    for message in messages:
        try:
            notification_id = _claim(db, message, hold_id=result.hold_id, booking_id=booking_id)
        except Exception:
            log.exception("Could not record %s %s notification", message.audience, message.channel)
            db.rollback()
            continue
        if notification_id is None:
            continue
        try:
            status, provider_id = _deliver(settings, message)
            _record(db, notification_id, status, provider_id, None)
        except Exception as error:
            log.warning("%s %s notification failed: %s", message.audience, message.channel, error)
            db.rollback()
            try:
                _record(db, notification_id, "failed", None, str(error)[:500])
            except Exception:
                log.exception("Could not record notification failure")
                db.rollback()


def notify_refund_settled(db: Session, settings: Settings, booking_id: int) -> None:
    """Best-effort, after the refund webhook has committed. Claimed once, because Square sends
    refund.created and refund.updated for one refund and redelivers both."""
    try:
        booking = db.get(Booking, booking_id)
        refund = db.scalar(select(Refund).where(Refund.booking_id == booking_id))
        if booking is None or refund is None:
            return
        body = owner_refund_settled_text(booking, refund.amount_cents, refund.status)
    except Exception:
        log.exception("Could not build refund notification for booking %s", booking_id)
        db.rollback()
        return
    notify_owner(db, settings, booking, body, event="refunded")


def notify_owner(db: Session, settings: Settings, booking: Booking, body: str, event: str) -> None:
    """A parent-driven notice about a booking: cancelled, moved, added to. Best-effort and after commit, the
    same as every other notification, so a Twilio outage can never undo what the parent just did."""
    for message in _owner_messages(settings, body, event=event):
        try:
            notification_id = _claim(db, message, hold_id=booking.hold_id, booking_id=booking.id)
        except Exception:
            log.exception("Could not record owner %s notification", event)
            db.rollback()
            continue
        if notification_id is None:
            continue
        try:
            status, provider_id = _deliver(settings, message)
            _record(db, notification_id, status, provider_id, None)
        except Exception as error:
            log.warning("owner %s notification failed: %s", event, error)
            db.rollback()
            try:
                _record(db, notification_id, "failed", None, str(error)[:500])
            except Exception:
                log.exception("Could not record notification failure")
                db.rollback()


def notify_new_request(db: Session, settings: Settings, request_id: int) -> None:
    """Best-effort owner notice for a new party request. Same rules as booking notices: after commit, never raises."""
    try:
        request = db.get(PartyRequest, request_id)
        if request is None:
            return
        messages = _owner_messages(settings, owner_request_text(request), event="request")
    except Exception:
        log.exception("Could not build request notification for request %s", request_id)
        db.rollback()
        return
    for message in messages:
        try:
            notification_id = _claim(db, message, request_id=request_id)
        except Exception:
            log.exception("Could not record request %s notification", request_id)
            db.rollback()
            continue
        if notification_id is None:
            continue
        try:
            status, provider_id = _deliver(settings, message)
            _record(db, notification_id, status, provider_id, None)
        except Exception as error:
            log.warning("owner %s request notification failed: %s", message.channel, error)
            db.rollback()
            try:
                _record(db, notification_id, "failed", None, str(error)[:500])
            except Exception:
                log.exception("Could not record request notification failure")
                db.rollback()
