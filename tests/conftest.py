import json
import os
from collections.abc import Iterator
from email.message import EmailMessage
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session


class _TestDatabase(BaseSettings):
    """Reads only TEST_DATABASE_URL, from the environment or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    test_database_url: str


TEST_DATABASE_URL = _TestDatabase().test_database_url

if urlsplit(TEST_DATABASE_URL).hostname not in {"localhost", "127.0.0.1", "db", "postgres"}:
    raise RuntimeError("TEST_DATABASE_URL must be a local database: the test suite truncates every table.")

# The app under test never reads .env, so real credentials in it cannot reach a test run.
os.environ["ENCORE_ENV_FILE"] = os.devnull
TEST_ENV = {
    "DATABASE_URL": TEST_DATABASE_URL,
    "FRONTEND_ORIGIN": "http://testserver",
    "SERVE_FRONTEND": "false",
    "MIN_LEAD_DAYS": "2",
    "TAX_MODE": "included",
    "TAX_RATE_PERCENT": "0",
    "WEEKEND_DEFAULT": "open",
    "WEEKENDS_AHEAD": "8",
    "ADMIN_PASSWORD": "test-admin-password",
    "SESSION_SECRET": "test-session-secret",
    "TRUST_PROXY_HEADERS": "false",
    "SQUARE_ENVIRONMENT": "sandbox",
    "SQUARE_ACCESS_TOKEN": "test-square-token",
    "SQUARE_LOCATION_ID": "LTESTLOCATION",
    "SQUARE_WEBHOOK_SIGNATURE_KEY": "test-signature-key",
    "SQUARE_WEBHOOK_NOTIFICATION_URL": "https://encore-test.example.com/api/webhooks/square",
    "DEV_FAKE_SQUARE": "false",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "TWILIO_FROM_NUMBER": "",
    "OWNER_PHONE": "+19145559999",
    "DISCORD_WEBHOOK_URL": "",
    "SMTP_HOST": "",
    "SMTP_USERNAME": "",
    "SMTP_PASSWORD": "",
    "EMAIL_FROM": "",
}
os.environ.update(TEST_ENV)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app import notifications  # noqa: E402
from app.services import square  # noqa: E402

TABLES = ("notifications", "payment_flags", "square_events", "bookings", "holds", "requests", "waitlist", "slots")


def _migrate(url: str) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    get_settings.cache_clear()
    get_engine.cache_clear()
    _migrate(TEST_DATABASE_URL)
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def clean_tables(migrated_database: None) -> Iterator[None]:
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def engine() -> Engine:
    return get_engine()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session


class FakeSquareApi:
    """Stands in for Square's REST API. Records every request; never touches the network."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.fail_with: int | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"path": request.url.path, "headers": dict(request.headers), "body": body})
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"errors": [{"code": "INTERNAL_SERVER_ERROR"}]})
        key = body["idempotency_key"]
        return httpx.Response(
            200,
            json={
                "payment_link": {
                    "id": f"link-{key}",
                    "url": f"https://sandbox.square.link/u/{key[:8]}",
                    "order_id": f"order-{key}",
                }
            },
        )


@pytest.fixture(autouse=True)
def square_api(monkeypatch: pytest.MonkeyPatch) -> FakeSquareApi:
    fake = FakeSquareApi()

    def client(settings: object) -> httpx.Client:
        return httpx.Client(base_url="https://connect.squareupsandbox.com", transport=httpx.MockTransport(fake.handle))

    monkeypatch.setattr(square, "http_client", client)
    return fake


class FakeMessaging:
    """Stands in for Twilio and Discord. Records every request; never touches the network."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.twilio_status = 201
        self.discord_status = 200
        self.raise_error: Exception | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_error:
            raise self.raise_error
        if request.url.host == "api.twilio.com":
            if self.twilio_status != 201:
                return httpx.Response(self.twilio_status, json={"code": 21608, "message": "Unverified number"})
            return httpx.Response(201, json={"sid": f"SM{len(self.requests):032d}", "status": "queued"})
        if self.discord_status != 200:
            return httpx.Response(self.discord_status, json={"message": "Unknown Webhook"})
        return httpx.Response(200, json={"id": f"{len(self.requests)}"})


@pytest.fixture(autouse=True)
def messaging(monkeypatch: pytest.MonkeyPatch) -> FakeMessaging:
    fake = FakeMessaging()
    monkeypatch.setattr(notifications, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handle)))
    return fake


class FakeSmtp:
    """Stands in for an SMTP server. Records every message; never touches the network."""

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.logins: list[tuple[str, str]] = []
        self.starttls_calls = 0
        self.connections: list[tuple[str, int]] = []
        self.fail_with: Exception | None = None

    def client(self, settings: object) -> "FakeSmtpConnection":
        return FakeSmtpConnection(self, getattr(settings, "smtp_host"), getattr(settings, "smtp_port"))


class FakeSmtpConnection:
    def __init__(self, server: FakeSmtp, host: str, port: int) -> None:
        self.server = server
        server.connections.append((host, port))

    def __enter__(self) -> "FakeSmtpConnection":
        if self.server.fail_with:
            raise self.server.fail_with
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.server.starttls_calls += 1

    def login(self, username: str, password: str) -> None:
        self.server.logins.append((username, password))

    def send_message(self, message: EmailMessage) -> None:
        self.server.messages.append(message)


@pytest.fixture(autouse=True)
def smtp(monkeypatch: pytest.MonkeyPatch) -> FakeSmtp:
    fake = FakeSmtp()
    monkeypatch.setattr(notifications, "smtp_client", fake.client)
    return fake
