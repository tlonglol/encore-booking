import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Time,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

HOLD_STATUSES = ("pending_payment", "booked", "paid_after_expiry", "expired", "cancelled")
REFUND_STATUSES = ("pending_approval", "pending", "completed", "failed", "rejected")
NOTIFICATION_EVENTS = ("booked", "late_payment", "cancelled", "rescheduled", "addons_changed", "request")
# A booking notice must survive Square redelivering the same event; the rest are single parent requests.
CLAIMED_ONCE_EVENTS = ("booked", "late_payment")
ACTIVE_HOLD_STATUSES = ("pending_payment", "booked")
PACKAGES = ("standard", "plus", "premium")
REQUEST_STATUSES = ("new", "approved", "declined", "expired", "converted")
FLAG_KINDS = ("paid_after_expiry", "amount_mismatch", "unmatched_order", "duplicate_payment")
EVENT_OUTCOMES = (
    "booked",
    "duplicate",
    "duplicate_payment",
    "expired_hold",
    "unmatched",
    "amount_mismatch",
    "refund_settled",
    "refund_unmatched",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    pass


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), unique=True)
    local_date: Mapped[date] = mapped_column(Date, index=True)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Hold(Base):
    __tablename__ = "holds"
    __table_args__ = (
        CheckConstraint(_in("status", HOLD_STATUSES), name="holds_status_valid"),
        CheckConstraint(_in("package", PACKAGES), name="holds_package_valid"),
        CheckConstraint("kids BETWEEN 1 AND 30", name="holds_kids_range"),
        CheckConstraint("total_cents >= 0 AND deposit_cents > 0", name="holds_money_positive"),
        # A hold that carries a payment link was checked out, so its snapshot and contact are complete.
        CheckConstraint(
            "square_order_id IS NULL OR (package IS NOT NULL AND kids IS NOT NULL AND total_cents IS NOT NULL"
            " AND first_name IS NOT NULL AND phone IS NOT NULL AND email IS NOT NULL)",
            name="holds_checkout_complete",
        ),
        # The slot-exclusivity guarantee: at most one pending or booked hold per slot.
        Index(
            "holds_one_active_per_slot",
            "slot_id",
            unique=True,
            postgresql_where=text(_in("status", ACTIVE_HOLD_STATUSES)),
        ),
        Index(
            "holds_square_order_id_key",
            "square_order_id",
            unique=True,
            postgresql_where=text("square_order_id IS NOT NULL"),
        ),
        Index("holds_pending_phone", "phone", postgresql_where=text("status = 'pending_payment'")),
        Index("holds_pending_email", "email", postgresql_where=text("status = 'pending_payment'")),
        Index("holds_pending_client_ip", "client_ip", postgresql_where=text("status = 'pending_payment'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    slot_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("slots.id"))
    status: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Snapshot and contact are NULL until checkout: the funnel holds the slot first and collects the rest later.
    package: Mapped[str | None] = mapped_column(Text)
    kids: Mapped[int | None] = mapped_column(SmallInteger)
    addons: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    line_items: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    total_cents: Mapped[int | None] = mapped_column(Integer)
    deposit_cents: Mapped[int] = mapped_column(Integer)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    kid_name: Mapped[str | None] = mapped_column(Text)
    kid_age: Mapped[int | None] = mapped_column(SmallInteger)
    birthday: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(INET)
    square_order_id: Mapped[str | None] = mapped_column(Text)
    square_payment_link_id: Mapped[str | None] = mapped_column(Text)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(_in("package", PACKAGES), name="bookings_package_valid"),
        CheckConstraint("kids BETWEEN 1 AND 30", name="bookings_kids_range"),
        CheckConstraint(
            "cancel_source IS NULL OR cancel_source IN ('parent', 'owner')", name="bookings_cancel_source_valid"
        ),
        # Second, independent guard: at most one live booking per slot.
        Index(
            "bookings_one_live_per_slot",
            "slot_id",
            unique=True,
            postgresql_where=text("cancelled_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    hold_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("holds.id"), unique=True)
    slot_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("slots.id"))
    confirmation_code: Mapped[str] = mapped_column(Text, unique=True)
    # The no-login manage page's only credential. Longer than the confirmation code, never shown in admin,
    # never logged, and never written into the calendar file the way the confirmation code is.
    manage_token: Mapped[str] = mapped_column(Text, unique=True)
    package: Mapped[str] = mapped_column(Text)
    kids: Mapped[int] = mapped_column(SmallInteger)
    addons: Mapped[dict[str, Any]] = mapped_column(JSONB)
    line_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    total_cents: Mapped[int] = mapped_column(Integer)
    deposit_cents: Mapped[int] = mapped_column(Integer)
    first_name: Mapped[str] = mapped_column(Text)
    last_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    kid_name: Mapped[str | None] = mapped_column(Text)
    kid_age: Mapped[int | None] = mapped_column(SmallInteger)
    birthday: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    square_payment_id: Mapped[str] = mapped_column(Text, unique=True)
    square_order_id: Mapped[str] = mapped_column(Text)
    paid_amount_cents: Mapped[int] = mapped_column(Integer)
    event_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Every move, for the owner's benefit; the courtesy flag is the one promise the rules actually spend.
    reschedule_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    rescheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    courtesy_reschedule_used: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    cancel_source: Mapped[str | None] = mapped_column(Text)


class PartyRequest(Base):
    """A weekday (or request-mode weekend) party inquiry. No hold, no payment; the owner follows up."""

    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint(_in("status", REQUEST_STATUSES), name="requests_status_valid"),
        CheckConstraint(_in("package", PACKAGES), name="requests_package_valid"),
        CheckConstraint("kids BETWEEN 1 AND 30", name="requests_kids_range"),
        CheckConstraint("total_cents >= 0", name="requests_total_positive"),
        # One open request per parent per date, by phone and by email.
        Index(
            "requests_one_new_per_phone_date",
            "phone",
            "local_date",
            unique=True,
            postgresql_where=text("status = 'new'"),
        ),
        Index(
            "requests_one_new_per_email_date",
            "email",
            "local_date",
            unique=True,
            postgresql_where=text("status = 'new'"),
        ),
        Index("requests_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    local_date: Mapped[date] = mapped_column(Date)
    preferred_time: Mapped[time] = mapped_column(Time)
    status: Mapped[str] = mapped_column(Text, default="new", server_default=text("'new'"))
    package: Mapped[str] = mapped_column(Text)
    kids: Mapped[int] = mapped_column(SmallInteger)
    addons: Mapped[dict[str, Any]] = mapped_column(JSONB)
    line_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    total_cents: Mapped[int] = mapped_column(Integer)
    deposit_cents: Mapped[int] = mapped_column(Integer)
    first_name: Mapped[str] = mapped_column(Text)
    last_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    kid_name: Mapped[str | None] = mapped_column(Text)
    kid_age: Mapped[int | None] = mapped_column(SmallInteger)
    birthday: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WaitlistEntry(Base):
    # A parent who wants to hear when new dates open. Email only, captured from the grid page.
    __tablename__ = "waitlist"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    client_ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SquareEvent(Base):
    __tablename__ = "square_events"
    __table_args__ = (CheckConstraint(_in("outcome", EVENT_OUTCOMES), name="square_events_outcome_valid"),)

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    payment_id: Mapped[str | None] = mapped_column(Text)
    order_id: Mapped[str | None] = mapped_column(Text)
    refund_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentFlag(Base):
    __tablename__ = "payment_flags"
    __table_args__ = (CheckConstraint(_in("kind", FLAG_KINDS), name="payment_flags_kind_valid"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    hold_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("holds.id"))
    # One flag per payment: a payment ends up as exactly one booking or one flag.
    square_payment_id: Mapped[str] = mapped_column(Text, unique=True)
    square_order_id: Mapped[str | None] = mapped_column(Text)
    amount_cents: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Refund(Base):
    """One deposit refund. Written before Square is called, so a refund that never reaches Square is visible
    rather than lost, and settled by Square's refund webhook afterwards."""

    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(_in("status", REFUND_STATUSES), name="refunds_status_valid"),
        CheckConstraint("amount_cents > 0", name="refunds_amount_positive"),
        # One deposit, one refund: both the daily cap and the webhook rely on this.
        Index("refunds_one_per_booking", "booking_id", unique=True),
        Index(
            "refunds_square_refund_id_key",
            "square_refund_id",
            unique=True,
            postgresql_where=text("square_refund_id IS NOT NULL"),
        ),
        Index("refunds_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    booking_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bookings.id"))
    square_payment_id: Mapped[str] = mapped_column(Text)
    square_refund_id: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    # Why it is waiting for the owner instead of having been sent: the daily cap, or a repeat-canceller flag.
    queued_reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("audience IN ('parent', 'owner')", name="notifications_audience_valid"),
        CheckConstraint("channel IN ('sms', 'discord', 'email')", name="notifications_channel_valid"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'logged')", name="notifications_status_valid"
        ),
        CheckConstraint(_in("event", NOTIFICATION_EVENTS), name="notifications_event_valid"),
        # A webhook-driven outcome is announced at most once per audience and channel however many times
        # Square redelivers it. Parent-driven notices (a cancellation, a move, an add-on) are single requests
        # that can legitimately happen more than once per hold, so they are recorded rather than claimed.
        Index(
            "notifications_once_per_hold",
            "hold_id",
            "audience",
            "channel",
            unique=True,
            postgresql_where=text(_in("event", CLAIMED_ONCE_EVENTS)),
        ),
        Index(
            "notifications_once_per_request",
            "request_id",
            "audience",
            "channel",
            unique=True,
            postgresql_where=text("request_id IS NOT NULL"),
        ),
        CheckConstraint("(hold_id IS NULL) <> (request_id IS NULL)", name="notifications_one_subject"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    hold_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("holds.id"))
    request_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("requests.id"))
    booking_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("bookings.id"))
    audience: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    event: Mapped[str] = mapped_column(Text, server_default=text("'booked'"))
    recipient: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
