import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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
ACTIVE_HOLD_STATUSES = ("pending_payment", "booked")
PACKAGES = ("standard", "plus", "premium")
FLAG_KINDS = ("paid_after_expiry", "amount_mismatch", "unmatched_order", "duplicate_payment")
EVENT_OUTCOMES = ("booked", "duplicate", "duplicate_payment", "expired_hold", "unmatched", "amount_mismatch")


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
    notes: Mapped[str | None] = mapped_column(Text)
    square_payment_id: Mapped[str] = mapped_column(Text, unique=True)
    square_order_id: Mapped[str] = mapped_column(Text)
    paid_amount_cents: Mapped[int] = mapped_column(Integer)
    event_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)


class SquareEvent(Base):
    __tablename__ = "square_events"
    __table_args__ = (CheckConstraint(_in("outcome", EVENT_OUTCOMES), name="square_events_outcome_valid"),)

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    payment_id: Mapped[str | None] = mapped_column(Text)
    order_id: Mapped[str | None] = mapped_column(Text)
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


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("audience IN ('parent', 'owner')", name="notifications_audience_valid"),
        CheckConstraint("channel IN ('sms', 'discord')", name="notifications_channel_valid"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'logged')", name="notifications_status_valid"
        ),
        # A hold's outcome is announced at most once per audience and channel.
        Index("notifications_once_per_hold", "hold_id", "audience", "channel", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    hold_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("holds.id"))
    booking_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("bookings.id"))
    audience: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    recipient: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
