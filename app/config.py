import os
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Tests point ENCORE_ENV_FILE at /dev/null so a developer's .env can never leak real credentials into a test run.
    model_config = SettingsConfigDict(env_file=os.environ.get("ENCORE_ENV_FILE", ".env"), extra="ignore")

    database_url: str
    frontend_origin: str = "http://localhost:8000"
    serve_frontend: bool = False
    min_lead_days: int = 2
    # How the receipt states tax. "included": prices already include it, the receipt says so.
    # "line_item": a server-computed Sales tax line is added to every quote at tax_rate_percent.
    # A decimal percent (New Rochelle is 8.375), kept exact: rates like that are not whole basis points.
    tax_mode: Literal["included", "line_item"] = "included"
    tax_rate_percent: Decimal = Decimal("0")
    weekend_default: Literal["open", "request"] = "open"
    weekends_ahead: int = 8
    # How long before the party self-serve add-on additions close. The food has to be ordered, so this
    # is the owner's number to confirm before launch.
    addon_cutoff_hours: int = 48
    # How many deposits may refund themselves in one day before the rest wait for the owner to approve.
    # An automatic refund moves real money with nobody in the loop, so there is a number.
    refund_daily_cap: int = 3
    # The demo reset deletes bookings, so it stays off unless someone deliberately turns it on.
    demo_reset_enabled: bool = False
    admin_password: str = ""
    session_secret: str = ""
    trust_proxy_headers: bool = False
    square_environment: Literal["sandbox", "production"] = "sandbox"
    # The test-mode ribbon. Left unset it follows square_environment, so sandbox credentials can never be
    # demoed without the notice, and going live cannot leave the notice up. Set it only to override that.
    test_mode: bool | None = None
    square_access_token: str = ""
    square_location_id: str = ""
    square_webhook_signature_key: str = ""
    square_webhook_notification_url: str = ""
    # Local only: with no Square access token, checkout is a fake page that sends a signed webhook to ourselves.
    dev_fake_square: bool = False
    # Cloudflare Turnstile on the Confirm action. Off by default: the funnel loads no Cloudflare script and
    # skips verification entirely while this is false. The site key is public; the secret key never leaves here.
    turnstile_enabled: bool = False
    turnstile_site_key: str = ""
    turnstile_secret_key: str = ""
    # Twilio SMS. With any of these empty, messages are logged to the console instead of sent.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    owner_phone: str = ""
    google_review_url: str = ""
    discord_webhook_url: str = ""
    # Parent confirmation email over SMTP. With any of these empty, the email is logged to the console instead.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""

    @property
    def test_mode_enabled(self) -> bool:
        return self.square_environment == "sandbox" if self.test_mode is None else self.test_mode

    @property
    def twilio_enabled(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_number)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_username and self.smtp_password and self.email_from)

    @property
    def fake_square_enabled(self) -> bool:
        return self.dev_fake_square and not self.square_access_token and self.square_environment == "sandbox"


@lru_cache
def get_settings() -> Settings:
    return Settings()
