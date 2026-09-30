from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

MONEY_PATH_INDEXES = {
    "holds_one_active_per_slot": ("holds", "status = ANY (ARRAY['pending_payment'::text, 'booked'::text])"),
    "bookings_one_live_per_slot": ("bookings", "cancelled_at IS NULL"),
}


@pytest.mark.parametrize("name", sorted(MONEY_PATH_INDEXES))
def test_money_path_index_exists(engine: Engine, name: str) -> None:
    table, predicate = MONEY_PATH_INDEXES[name]
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT tablename, indexdef FROM pg_indexes WHERE indexname = :name"), {"name": name}
        ).one_or_none()
    assert row is not None, f"{name} is missing"
    assert row.tablename == table
    assert row.indexdef.startswith("CREATE UNIQUE INDEX")
    assert row.indexdef.endswith(f"(slot_id) WHERE ({predicate})")


def _insert_slot(engine: Engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO slots (starts_at, local_date) VALUES (:starts_at, :local_date) RETURNING id"),
            {"starts_at": datetime(2030, 1, 5, 15, tzinfo=timezone.utc), "local_date": date(2030, 1, 5)},
        ).scalar_one()


def _insert_hold(engine: Engine, slot_id: int, status: str, phone: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO holds (slot_id, status, expires_at, package, kids, addons, line_items,"
                " total_cents, deposit_cents, first_name, last_name, phone, email)"
                " VALUES (:slot_id, :status, :expires_at, 'standard', 10, '{}', '[]', 39900, 10000,"
                " 'A', 'B', :phone, :email)"
            ),
            {
                "slot_id": slot_id,
                "status": status,
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
                "phone": phone,
                "email": f"{phone}@example.com",
            },
        )


def test_second_active_hold_on_slot_is_rejected_by_database(engine: Engine) -> None:
    slot_id = _insert_slot(engine)
    _insert_hold(engine, slot_id, "pending_payment", "+15550000001")
    with pytest.raises(IntegrityError, match="holds_one_active_per_slot"):
        _insert_hold(engine, slot_id, "pending_payment", "+15550000002")
    with pytest.raises(IntegrityError, match="holds_one_active_per_slot"):
        _insert_hold(engine, slot_id, "booked", "+15550000003")


def test_inactive_holds_do_not_occupy_slot(engine: Engine) -> None:
    slot_id = _insert_slot(engine)
    for index, status in enumerate(("expired", "cancelled", "paid_after_expiry")):
        _insert_hold(engine, slot_id, status, f"+1555000001{index}")
    _insert_hold(engine, slot_id, "pending_payment", "+15550000020")
