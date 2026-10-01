from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from tests.helpers import checkout, client_from, make_hold, make_slot, next_saturday, start_hold, today


def test_sixth_hold_from_one_ip_returns_429(db: Session) -> None:
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 7)]
    client = client_from("198.51.100.10")
    created = [start_hold(client, slot.id) for slot in slots[:5]]
    assert [r.status_code for r in created] == [201] * 5
    response = start_hold(client, slots[5].id)
    assert response.status_code == 429
    assert "network" in response.json()["detail"]
    assert response.json()["expires_at"] == min(r.json()["expires_at"] for r in created).replace("Z", "+00:00")


def test_checkout_with_a_phone_already_holding_returns_429(db: Session) -> None:
    first = make_slot(db, next_saturday(7))
    second = make_slot(db, next_saturday(14))
    client = client_from("198.51.100.7")
    held = start_hold(client, first.id)
    assert checkout(client, held.json()["hold_id"], 1).status_code == 200
    other = start_hold(client, second.id).json()["hold_id"]
    response = checkout(client, other, 2, phone="914.555.0001")
    assert response.status_code == 429
    assert "phone" in response.json()["detail"]
    assert response.json()["expires_at"] == held.json()["expires_at"].replace("Z", "+00:00")


def test_checkout_with_an_email_already_holding_returns_429(db: Session) -> None:
    first = make_slot(db, next_saturday(7))
    second = make_slot(db, next_saturday(14))
    client = client_from("198.51.100.9")
    assert checkout(client, start_hold(client, first.id).json()["hold_id"], 1).status_code == 200
    other = start_hold(client, second.id).json()["hold_id"]
    response = checkout(client, other, 2, email="PARENT1@example.com")
    assert response.status_code == 429
    assert "email" in response.json()["detail"]


def test_hold_inside_lead_time_is_rejected(db: Session) -> None:
    too_soon = make_slot(db, today() + timedelta(days=1))
    response = start_hold(client_from("198.51.100.8"), too_soon.id)
    assert response.status_code == 422
    assert "too soon" in response.json()["detail"]


def test_spoofed_forwarded_for_does_not_bypass_ip_cap(db: Session) -> None:
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 7)]
    client = client_from("198.51.100.11")
    for n, slot in enumerate(slots):
        response = client.post(
            "/api/holds", json={"slot_id": slot.id}, headers={"X-Forwarded-For": f"10.0.0.{n}"}
        )
    assert response.status_code == 429


def test_forwarded_for_is_used_behind_a_trusted_proxy(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
    slots = [make_slot(db, next_saturday(7 * week)) for week in range(1, 7)]
    proxy = client_from("127.0.0.1")
    for n, slot in enumerate(slots):
        response = proxy.post(
            "/api/holds", json={"slot_id": slot.id}, headers={"X-Forwarded-For": f"6.6.6.6, 192.0.2.{n + 1}"}
        )
        assert response.status_code == 201, "each request comes from a different real client"


def test_expired_hold_does_not_count_toward_caps(db: Session) -> None:
    old_slot = make_slot(db, next_saturday(7))
    make_hold(db, old_slot, expires_in=timedelta(minutes=-1), phone="+19145550001")
    new_slot = make_slot(db, next_saturday(14))
    client = client_from("198.51.100.12")
    assert checkout(client, start_hold(client, new_slot.id).json()["hold_id"], 1).status_code == 200


def test_booked_party_does_not_count_toward_caps(db: Session) -> None:
    booked_slot = make_slot(db, next_saturday(7))
    make_hold(db, booked_slot, status="booked", phone="+19145550001")
    new_slot = make_slot(db, next_saturday(14))
    client = client_from("198.51.100.13")
    assert checkout(client, start_hold(client, new_slot.id).json()["hold_id"], 1).status_code == 200
