from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from tests.helpers import client_from, hold_body, make_hold, make_slot, next_saturday, today


def test_second_active_hold_for_same_phone_returns_429(db: Session) -> None:
    first = make_slot(db, next_saturday(7))
    second = make_slot(db, next_saturday(14))
    client = client_from("198.51.100.7")
    created = client.post("/api/holds", json=hold_body(first.id, 1))
    assert created.status_code == 201
    response = client.post("/api/holds", json=hold_body(second.id, 2, phone="914.555.0001"))
    assert response.status_code == 429
    assert "phone" in response.json()["detail"]
    assert response.json()["expires_at"] == created.json()["expires_at"].replace("Z", "+00:00")


def test_hold_inside_lead_time_is_rejected(db: Session) -> None:
    too_soon = make_slot(db, today() + timedelta(days=1))
    response = client_from("198.51.100.8").post("/api/holds", json=hold_body(too_soon.id, 1))
    assert response.status_code == 422
    assert "too soon" in response.json()["detail"]


def test_second_active_hold_for_same_email_returns_429(db: Session) -> None:
    first = make_slot(db, next_saturday(7))
    second = make_slot(db, next_saturday(14))
    client = client_from("198.51.100.9")
    assert client.post("/api/holds", json=hold_body(first.id, 1)).status_code == 201
    response = client.post("/api/holds", json=hold_body(second.id, 2, email="PARENT1@example.com"))
    assert response.status_code == 429
    assert "email" in response.json()["detail"]


def test_five_holds_per_ip_then_429(db: Session) -> None:
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 7)]
    client = client_from("198.51.100.10")
    for n, slot in enumerate(slots[:5]):
        assert client.post("/api/holds", json=hold_body(slot.id, n)).status_code == 201
    response = client.post("/api/holds", json=hold_body(slots[5].id, 99))
    assert response.status_code == 429
    assert "network" in response.json()["detail"]


def test_spoofed_forwarded_for_does_not_bypass_ip_cap(db: Session) -> None:
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 7)]
    client = client_from("198.51.100.11")
    for n, slot in enumerate(slots):
        response = client.post("/api/holds", json=hold_body(slot.id, n), headers={"X-Forwarded-For": f"10.0.0.{n}"})
    assert response.status_code == 429


def test_forwarded_for_is_used_behind_a_trusted_proxy(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 7)]
    proxy = client_from("127.0.0.1")
    for n, slot in enumerate(slots):
        response = proxy.post(
            "/api/holds", json=hold_body(slot.id, n), headers={"X-Forwarded-For": f"6.6.6.6, 192.0.2.{n + 1}"}
        )
        assert response.status_code == 201, "each request comes from a different real client"


def test_expired_hold_does_not_count_toward_caps(db: Session) -> None:
    old_slot = make_slot(db, next_saturday(7))
    make_hold(db, old_slot, expires_in=timedelta(minutes=-1), phone="+19145550001")
    new_slot = make_slot(db, next_saturday(14))
    assert client_from("198.51.100.12").post("/api/holds", json=hold_body(new_slot.id, 1)).status_code == 201


def test_booked_party_does_not_count_toward_caps(db: Session) -> None:
    booked_slot = make_slot(db, next_saturday(7))
    make_hold(db, booked_slot, status="booked", phone="+19145550001")
    new_slot = make_slot(db, next_saturday(14))
    assert client_from("198.51.100.13").post("/api/holds", json=hold_body(new_slot.id, 1)).status_code == 201
