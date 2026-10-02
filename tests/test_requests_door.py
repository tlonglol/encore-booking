"""The weekday-request front door, shut on both sides.

The funnel offers no request step, so the endpoint behind it should not answer either: a closed door that
still responds is a door. The table, the service and the admin list all stay, so reopening it is one
environment variable rather than a rebuild.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from tests.helpers import admin_client, client, today


@pytest.fixture
def reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _weekday(days_ahead: int = 7) -> date:
    day = today() + timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _body(day: date) -> dict[str, object]:
    return {
        "date": day.isoformat(),
        "preferred_time": "16:00",
        "package": "standard",
        "kids": 18,
        "addons": {},
        "first_name": "Pat",
        "last_name": "Parent",
        "phone": "(914) 555-0199",
        "email": "pat@example.com",
    }


def test_the_door_is_shut_by_default(db: Session, reset_settings) -> None:
    assert get_settings().requests_enabled is False
    response = client().post("/api/requests", json=_body(_weekday()))
    assert response.status_code == 404
    assert db.execute(text("SELECT count(*) FROM requests")).scalar_one() == 0


def test_a_shut_door_does_not_leak_why_it_is_shut(db: Session, reset_settings) -> None:
    """404 and nothing else: a 403 would advertise that there is something here to turn on."""
    response = client().post("/api/requests", json=_body(_weekday()))
    assert response.json() == {"detail": "Not found"}


def test_a_shut_door_refuses_before_it_validates(db: Session, reset_settings) -> None:
    """Rubbish still gets a 404, not a 422: the endpoint should not behave like one that exists."""
    response = client().post("/api/requests", json={"date": "nonsense"})
    assert response.status_code == 404


def test_the_door_opens_with_one_variable(db: Session, monkeypatch: pytest.MonkeyPatch, reset_settings) -> None:
    monkeypatch.setenv("REQUESTS_ENABLED", "true")
    get_settings.cache_clear()
    response = client().post("/api/requests", json=_body(_weekday()))
    assert response.status_code == 201, response.text
    assert db.execute(text("SELECT count(*) FROM requests")).scalar_one() == 1


def test_the_admin_list_keeps_working_while_the_door_is_shut(
    db: Session, monkeypatch: pytest.MonkeyPatch, reset_settings
) -> None:
    """Requests taken before the flag went off are not lost, and the owner can still read them."""
    monkeypatch.setenv("REQUESTS_ENABLED", "true")
    get_settings.cache_clear()
    assert client().post("/api/requests", json=_body(_weekday())).status_code == 201

    monkeypatch.setenv("REQUESTS_ENABLED", "false")
    get_settings.cache_clear()
    rows = admin_client().get("/api/admin/requests").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "new"
