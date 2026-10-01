import os
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
    weekend_default: Literal["open", "request"] = "open"
    weekends_ahead: int = 8
    admin_password: str = ""
    session_secret: str = ""
    trust_proxy_headers: bool = False
    square_environment: Literal["sandbox", "production"] = "sandbox"
    square_access_token: str = ""
    square_location_id: str = ""
    square_webhook_signature_key: str = ""
    square_webhook_notification_url: str = ""
    # Local only: with no Square access token, checkout is a fake page that sends a signed webhook to ourselves.
    dev_fake_square: bool = False
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
