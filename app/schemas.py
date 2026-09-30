from datetime import date, datetime, time

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
    slot_id: int
    package: str
    kids: int
    addons: dict[str, int] = Field(default_factory=dict)
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
    phone: str = Field(min_length=7, max_length=30)
    email: EmailStr
    notes: str | None = Field(default=None, max_length=1000)


class HoldCreatedOut(BaseModel):
    hold_id: str
    checkout_url: str | None
    expires_at: datetime
    seconds_remaining: int


class SlotSummary(BaseModel):
    id: int
    date: str
    day_label: str
    time_label: str


class PartyDetails(BaseModel):
    """Everything the confirmation page may show. Deliberately no phone, email, or notes."""

    confirmation_code: str | None
    first_name: str | None
    package: str
    package_label: str
    day_label: str
    time_label: str
    hours: int
    kids: int
    line_items: list[LineItemOut]
    total_cents: int
    deposit_cents: int
    balance_cents: int


class HoldStatusOut(BaseModel):
    hold_id: str
    status: str
    expires_at: datetime
    seconds_remaining: int
    checkout_url: str | None
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
    notes: str | None = Field(default=None, max_length=1000)


class RequestOut(BaseModel):
    request_id: int
    day_label: str
    time_label: str
    package_label: str
    kids: int
    total_cents: int
