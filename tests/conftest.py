import os
from collections.abc import Iterator
from urllib.parse import urlsplit

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
    "WEEKENDS_AHEAD": "8",
    "ADMIN_PASSWORD": "test-admin-password",
    "SESSION_SECRET": "test-session-secret",
}
os.environ.update(TEST_ENV)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402

TABLES = ("notifications", "payment_flags", "square_events", "bookings", "holds", "slots")


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
