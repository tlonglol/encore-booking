from sqlalchemy.orm import Session

from tests.helpers import client, create_hold_via_api, deliver, payment_event


def test_booking_lookup_by_code(db: Session) -> None:
    """The permanent confirmation URL. It is reachable by anyone holding the code, so it must answer with
    the party and nothing that identifies the parent."""
    hold_id, ref = create_hold_via_api(db)
    deliver(payment_event(ref))
    code = client().get(f"/api/holds/{hold_id}").json()["party"]["confirmation_code"]
    response = client().get(f"/api/bookings/{code.lower()}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "booked"
    assert body["party"]["balance_cents"] == 44400
    assert "+1914" not in response.text and "@example.com" not in response.text
    assert client().get("/api/bookings/AAAAAAAAAA").status_code == 404
    assert client().get("/api/bookings/short").status_code == 404
