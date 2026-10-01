from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.helpers import admin_client, client, client_from, create_hold_via_api, deliver, payment_event, start_hold


def _book(db: Session, days_ahead: int = 7, n: int = 1) -> tuple[str, str]:
    hold_id, order_id = create_hold_via_api(db, days_ahead, n)
    deliver(payment_event(order_id, payment_id=f"pay-{hold_id}"))
    code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]
    return hold_id, code


def test_admin_endpoints_require_login() -> None:
    anonymous = client()
    for path in ("/api/admin/bookings", "/api/admin/flags", "/api/admin/notifications", "/api/admin/session"):
        assert anonymous.get(path).status_code == 401
    assert anonymous.post("/api/admin/bookings/1/cancel", json={}).status_code == 401
    assert anonymous.post("/api/admin/flags/1/resolve").status_code == 401


def test_bookings_list_shows_what_the_owner_needs(db: Session) -> None:
    _book(db)
    (booking,) = admin_client().get("/api/admin/bookings").json()
    assert booking["package"] == "Standard"
    assert booking["kids"] == 18
    assert booking["addons"] == "Pizza Pack x1"
    assert booking["total_cents"] == 54400
    assert booking["balance_cents"] == 44400
    assert booking["deposit_status"] == "paid"
    assert booking["phone"] == "+19145550001"
    assert booking["kid_name"] == "Sam"
    assert booking["kid_age"] == 8
    assert booking["notes"] is None, "the funnel form has no notes field"
    assert booking["time_label"] == "10:00 AM"
    assert booking["full_refund"] is True


def test_cancel_frees_the_slot_and_records_refund_data(db: Session, engine: Engine) -> None:
    hold_id, code = _book(db)
    admin = admin_client()
    (booking,) = admin.get("/api/admin/bookings").json()
    response = admin.post(f"/api/admin/bookings/{booking['id']}/cancel", json={"reason": "Parent called"})
    assert response.status_code == 200
    assert response.json()["full_refund"] is True
    assert admin.post(f"/api/admin/bookings/{booking['id']}/cancel", json={}).status_code == 409
    assert admin.post("/api/admin/bookings/999999/cancel", json={}).status_code == 404

    assert client().get(f"/api/bookings/{code}").json()["status"] == "cancelled"
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT b.created_at, b.event_starts_at, b.cancelled_at, h.status, h.slot_id FROM bookings b"
                 " JOIN holds h ON h.id = b.hold_id")
        ).one()
    assert row.cancelled_at is not None and row.status == "cancelled"
    retry = start_hold(client_from("203.0.113.200"), row.slot_id)
    assert retry.status_code == 201, "the cancelled date is bookable again"


def test_flags_are_visible_and_resolvable(db: Session, engine: Engine) -> None:
    hold_id, order_id = create_hold_via_api(db)
    with engine.begin() as conn:
        conn.execute(text("UPDATE holds SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": hold_id})
    deliver(payment_event(order_id, payment_id="pay-late"))
    admin = admin_client()
    (flag,) = admin.get("/api/admin/flags").json()
    assert flag["kind"] == "paid_after_expiry"
    assert flag["phone"] == "+19145550001"
    assert flag["resolved_at"] is None
    assert admin.post(f"/api/admin/flags/{flag['id']}/resolve").json() == {"ok": True}
    assert admin.get("/api/admin/flags").json()[0]["resolved_at"] is not None
    assert admin.post("/api/admin/flags/999999/resolve").status_code == 404


def test_notification_log_is_visible(db: Session) -> None:
    _book(db)
    rows = admin_client().get("/api/admin/notifications").json()
    assert {(row["audience"], row["status"]) for row in rows} == {("parent", "logged"), ("owner", "logged")}
