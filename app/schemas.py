from datetime import date, datetime, time

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class QuoteRequest(BaseModel):
    package: str
    kids: int
    addons: dict[str, int] = Field(default_factory=dict)


class LineItemOut(BaseModel):
    key: str
    label: str
    quantity: int
    unit_cents: int
    amount_cents: int
    included: bool


class QuoteOut(BaseModel):
    package: str
    kids: int
    addons: dict[str, int]
    hours: int
    line_items: list[LineItemOut]
    total_cents: int
    deposit_cents: int
    balance_cents: int


class HoldRequest(BaseModel):
    """Anonymous: tapping a date card holds the slot; contact details arrive at checkout."""

    slot_id: int
    replace_hold_id: UUID | None = None


class HoldCreatedOut(BaseModel):
    hold_id: str
    expires_at: datetime
    seconds_remaining: int
    date: str
    day_label: str
    time_label: str


class HoldEmailIn(BaseModel):
    email: EmailStr


class DraftIn(BaseModel):
    """What the parent has chosen so far. No prices: the server computes them."""

    package: str
    kids: int
    addons: dict[str, int] = Field(default_factory=dict)
    birthday: date | None = None


class CheckoutIn(BaseModel):
    package: str
    kids: int
    addons: dict[str, int] = Field(default_factory=dict)
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(default="", max_length=60)
    phone: str = Field(min_length=7, max_length=30)
    email: EmailStr
    kid_name: str = Field(min_length=1, max_length=60)
    kid_age: int = Field(ge=1, le=99)
    birthday: date | None = None


class SlotSummary(BaseModel):
    id: int
    date: str
    day_label: str
    time_label: str


class PartyDetails(BaseModel):
    """Everything the confirmation page may show. Deliberately no phone, email, or notes."""

    confirmation_code: str | None
    first_name: str | None
    # The birthday child, shown only once booked (never on a pending hold).
    kid_name: str | None = None
    kid_age: int | None = None
    birthday: date | None = None
    package: str
    package_label: str
    day_label: str
    time_label: str
    starts_at: datetime
    hours: int
    kids: int
    line_items: list[LineItemOut]
    total_cents: int
    deposit_cents: int
    balance_cents: int


class SlotInfo(BaseModel):
    date: str
    day_label: str
    time_label: str


class HoldStatusOut(BaseModel):
    hold_id: str
    status: str
    expires_at: datetime
    seconds_remaining: int
    checkout_url: str | None
    slot: SlotInfo
    party: PartyDetails | None


class CheckoutOut(BaseModel):
    checkout_url: str
    seconds_remaining: int
    party: PartyDetails


class BookingOut(BaseModel):
    status: str
    party: PartyDetails


class RequestCreate(BaseModel):
    date: date
    preferred_time: time
    package: str
    kids: int
    addons: dict[str, int] = Field(default_factory=dict)
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
    phone: str = Field(min_length=7, max_length=30)
    email: EmailStr
    kid_name: str | None = Field(default=None, max_length=60)
    kid_age: int | None = Field(default=None, ge=1, le=99)
    birthday: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


class WaitlistIn(BaseModel):
    email: EmailStr


class RequestOut(BaseModel):
    request_id: int
    day_label: str
    time_label: str
    package_label: str
    kids: int
    total_cents: int
