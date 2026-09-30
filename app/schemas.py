from pydantic import BaseModel, Field


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
