"""The public config endpoint: the only thing that tells the static frontend which flags are on."""

import pytest

from app.config import get_settings
from tests.helpers import client


@pytest.fixture
def reset_settings():
    """get_settings is cached, so a flag change has to invalidate it on the way in and on the way out."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_sandbox_credentials_turn_the_ribbon_on_without_being_asked(reset_settings) -> None:
    body = client().get("/api/config").json()
    assert body["test_mode"] is True


def test_test_mode_can_be_overridden_explicitly(monkeypatch: pytest.MonkeyPatch, reset_settings) -> None:
    monkeypatch.setenv("TEST_MODE", "false")
    get_settings.cache_clear()
    assert client().get("/api/config").json()["test_mode"] is False


def test_production_credentials_take_the_ribbon_down(monkeypatch: pytest.MonkeyPatch, reset_settings) -> None:
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "production")
    get_settings.cache_clear()
    assert client().get("/api/config").json()["test_mode"] is False


def test_turnstile_is_off_by_default_and_leaks_no_site_key(reset_settings) -> None:
    body = client().get("/api/config").json()
    assert body["turnstile_enabled"] is False
    assert body["turnstile_site_key"] == ""


def test_turnstile_site_key_is_published_only_once_enabled(monkeypatch: pytest.MonkeyPatch, reset_settings) -> None:
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "0x4AAAAAAATestSiteKey")
    get_settings.cache_clear()
    assert client().get("/api/config").json()["turnstile_site_key"] == ""

    monkeypatch.setenv("TURNSTILE_ENABLED", "true")
    get_settings.cache_clear()
    assert client().get("/api/config").json()["turnstile_site_key"] == "0x4AAAAAAATestSiteKey"
