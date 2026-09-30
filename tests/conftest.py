import os
from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

DEFAULT_TEST_URL = "postgresql+psycopg://encore:encore@localhost:5433/encore_test"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)

if urlsplit(TEST_DATABASE_URL).hostname not in {"localhost", "127.0.0.1", "db", "postgres"}:
    raise RuntimeError("TEST_DATABASE_URL must be a local database: the test suite truncates every table.")

# The app under test only ever sees the test database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402

TABLES = ("notifications", "payment_flags", "square_events", "bookings", "holds", "slots")


def _migrate_from_scratch(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    get_settings.cache_clear()
    get_engine.cache_clear()
    _migrate_from_scratch(TEST_DATABASE_URL)
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
