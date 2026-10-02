"""The deployed entrypoint, exercised as Lambda actually calls it.

Every other test in this suite talks to the ASGI app through TestClient. On AWS nothing talks to the
app directly: an API Gateway HTTP API event arrives, Mangum turns it into an ASGI scope, and the
response is turned back into a JSON envelope. That translation is where a deploy breaks in ways no TestClient test
can see, so these drive `app.lambda_handler.handler` with real payload-2.0 events and pin the four
things the money path depends on surviving it: the raw webhook body byte for byte, the client IP coming
from the event rather than a header, the anonymous session cookie round-tripping, and hold expiry still
being decided by the database clock with nothing sweeping.
"""

import base64
import importlib.util
import json
import pathlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
from mangum import Mangum
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.lambda_handler import handler
from app.services.square import SIGNATURE_HEADER, signature_for
from tests.helpers import WEBHOOK_KEY, WEBHOOK_URL, make_hold, make_slot, next_saturday, payment_event


def event(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    source_ip: str = "203.0.113.7",
    headers: dict[str, str] | None = None,
    cookies: list[str] | None = None,
    base64_body: bool = False,
) -> dict[str, Any]:
    """An API Gateway HTTP API request, payload format 2.0, shaped the way AWS sends it.

    A Lambda Function URL sends the identical format, so these events cover either front door.
    """
    request_headers = {"host": "encore-test.lambda-url.us-east-1.on.aws", "user-agent": "demo-phone"}
    request_headers.update({k.lower(): v for k, v in (headers or {}).items()})
    raw_path, _, query = path.partition("?")
    encoded: str | None = None
    if body is not None:
        encoded = base64.b64encode(body).decode() if base64_body else body.decode()
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": raw_path,
        "rawQueryString": query,
        "headers": request_headers,
        "cookies": cookies or [],
        "requestContext": {
            "http": {
                "method": method,
                "path": raw_path,
                "protocol": "HTTP/1.1",
                "sourceIp": source_ip,
                "userAgent": "demo-phone",
            },
            "requestId": str(uuid.uuid4()),
            "stage": "$default",
        },
        "body": encoded,
        "isBase64Encoded": base64_body if body is not None else False,
    }


def body_of(response: dict[str, Any]) -> str:
    if response.get("isBase64Encoded"):
        return base64.b64decode(response["body"]).decode()
    return response["body"]


def json_of(response: dict[str, Any]) -> Any:
    return json.loads(body_of(response))


def test_the_keep_warm_ping_never_reaches_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """The schedule exists to hold a container open, not to exercise anything. If the ping ever reached
    the app it would open a database connection every five minutes for no reason."""
    import app.lambda_handler as entrypoint

    def explode(*args: object) -> None:
        raise AssertionError("the warmer ping reached the ASGI app")

    monkeypatch.setattr(entrypoint, "_asgi", explode)
    assert entrypoint.handler({"warmer": True}, None) == {"warmed": True}


def test_an_ordinary_api_request_survives_the_translation() -> None:
    response = handler(event("GET", "/api/config"), None)
    assert response["statusCode"] == 200
    # Sandbox credentials, so the ribbon is on and the demo cannot be mistaken for real money.
    assert json_of(response)["test_mode"] is True


def test_a_query_string_arrives_intact() -> None:
    """Mangum reads the query off rawQueryString, which is a different field from the path. The grid
    asks for a window of weekends, so an empty query here would silently serve the default instead."""
    default = json_of(handler(event("GET", "/api/availability"), None))
    narrowed = json_of(handler(event("GET", "/api/availability?weekends=3"), None))
    assert default["weekends"] == 8
    assert narrowed["weekends"] == 3


def test_the_client_ip_is_the_event_source_ip_not_a_spoofable_header(db: Session) -> None:
    """The hold caps are built on the connection's source IP. API Gateway adds its own X-Forwarded-For,
    but it is not a proxy we put there to carry a client's address, so a client-sent one is ignored:
    the true client arrives at requestContext.http.sourceIp, which is where Mangum reads it."""
    slot = make_slot(db, next_saturday(7))
    response = handler(
        event(
            "POST",
            "/api/holds",
            body=json.dumps({"slot_id": slot.id}).encode(),
            source_ip="198.51.100.22",
            headers={"content-type": "application/json", "x-forwarded-for": "203.0.113.99"},
        ),
        None,
    )
    assert response["statusCode"] == 201, body_of(response)
    stored = db.execute(
        text("SELECT client_ip FROM holds WHERE id = :id"), {"id": json_of(response)["hold_id"]}
    ).scalar_one()
    assert str(stored) == "198.51.100.22"


def test_the_anonymous_session_cookie_round_trips_through_the_envelope(db: Session) -> None:
    """One hold per browser is enforced on the server by this cookie. Payload 2.0 does not put
    Set-Cookie in the headers map, it puts it in a `cookies` list, and the next request brings it back
    the same way: if that round trip breaks, every tab looks like a new browser and the guarantee is
    quietly gone."""
    first_slot = make_slot(db, next_saturday(7))
    second_slot = make_slot(db, next_saturday(14))

    created = handler(
        event("POST", "/api/holds", body=json.dumps({"slot_id": first_slot.id}).encode(),
              headers={"content-type": "application/json"}),
        None,
    )
    assert created["statusCode"] == 201
    jar = [cookie.split(";")[0] for cookie in created["cookies"]]
    assert any(cookie.startswith("encore_session=") for cookie in jar), created["cookies"]

    # The same browser takes a second date. The server should move the hold, not stack a second one.
    moved = handler(
        event("POST", "/api/holds", body=json.dumps({"slot_id": second_slot.id}).encode(),
              headers={"content-type": "application/json"}, cookies=jar),
        None,
    )
    assert moved["statusCode"] == 201, body_of(moved)
    live = db.execute(
        text("SELECT count(*) FROM holds WHERE status = 'pending_payment' AND expires_at > now()")
    ).scalar_one()
    assert live == 1


def test_the_webhook_gets_the_raw_body_byte_for_byte(db: Session) -> None:
    """Square signs (notification URL + raw body). Mangum hands the route the decoded bytes, and the
    signature only verifies if they are the exact bytes Square signed: a re-serialized body, or one
    round-tripped through a JSON parser, would differ and every real payment would 401."""
    slot = make_slot(db, next_saturday(7))
    hold = make_hold(db, slot, order_id="order-lambda-1")
    raw = payment_event("order-lambda-1", payment_id="pay-lambda-1")

    response = handler(
        event(
            "POST",
            "/api/webhooks/square",
            body=raw,
            headers={
                "content-type": "application/json",
                SIGNATURE_HEADER: signature_for(raw, WEBHOOK_KEY, WEBHOOK_URL),
            },
        ),
        None,
    )
    assert response["statusCode"] == 200, body_of(response)
    assert json_of(response)["outcome"] == "booked"
    booked = db.execute(text("SELECT count(*) FROM bookings WHERE hold_id = :id"), {"id": hold.id}).scalar_one()
    assert booked == 1


def test_a_base64_encoded_delivery_verifies_the_same(db: Session) -> None:
    """The gateway may hand the body over base64-encoded depending on how the sender framed it. The
    signature is over the decoded bytes either way, so both framings have to reach the route identically."""
    slot = make_slot(db, next_saturday(7))
    make_hold(db, slot, order_id="order-lambda-2")
    raw = payment_event("order-lambda-2", payment_id="pay-lambda-2")

    response = handler(
        event(
            "POST",
            "/api/webhooks/square",
            body=raw,
            base64_body=True,
            headers={
                "content-type": "application/json",
                SIGNATURE_HEADER: signature_for(raw, WEBHOOK_KEY, WEBHOOK_URL),
            },
        ),
        None,
    )
    assert response["statusCode"] == 200, body_of(response)
    assert json_of(response)["outcome"] == "booked"


def test_a_tampered_body_is_refused_through_the_handler(db: Session) -> None:
    """The deposit amount raised in flight, with the signature of the original. Verified before parsing,
    so the booking is never considered."""
    slot = make_slot(db, next_saturday(7))
    make_hold(db, slot, order_id="order-lambda-3")
    raw = payment_event("order-lambda-3", payment_id="pay-lambda-3")
    signature = signature_for(raw, WEBHOOK_KEY, WEBHOOK_URL)
    tampered = raw.replace(b'"amount": 10000', b'"amount": 10001')
    assert tampered != raw

    response = handler(
        event(
            "POST",
            "/api/webhooks/square",
            body=tampered,
            headers={"content-type": "application/json", SIGNATURE_HEADER: signature},
        ),
        None,
    )
    assert response["statusCode"] == 401
    assert db.execute(text("SELECT count(*) FROM bookings")).scalar_one() == 0


def test_a_lapsed_hold_reads_as_expired_through_the_handler(db: Session) -> None:
    """Lazy expiry is what makes this design correct on a platform that freezes the container between
    requests: no sweep runs, no timer survives, and the first read after the deadline is the one that
    notices. The same question asked through a Lambda event gets the same answer."""
    slot = make_slot(db, next_saturday(7))
    hold = make_hold(db, slot, expires_in=timedelta(minutes=-1))

    state = handler(event("GET", f"/api/holds/{hold.id}"), None)
    assert state["statusCode"] == 200
    assert json_of(state)["status"] == "expired"

    grid = json_of(handler(event("GET", "/api/availability"), None))["days"]
    assert next(d["status"] for d in grid if d["date"] == slot.local_date.isoformat()) == "open"

    # Nothing touched the row to make that true.
    assert db.execute(
        text("SELECT status FROM holds WHERE id = :id"), {"id": hold.id}
    ).scalar_one() == "pending_payment"


# ---- Static frontend and the /book/* catch-all, which the deploy serves from this same app ----


@pytest.fixture
def serving_handler() -> Any:
    """A second copy of the app with SERVE_FRONTEND on.

    The frontend mount is decided when app/main.py is imported, and the rest of the suite imports it
    with serving off. Loading the module a second time under its own name gives the deployed shape
    without disturbing the app every other test is using.
    """
    get_settings.cache_clear()
    import os

    previous = os.environ.get("SERVE_FRONTEND")
    os.environ["SERVE_FRONTEND"] = "true"
    try:
        spec = importlib.util.spec_from_file_location("encore_main_serving", pathlib.Path("app/main.py"))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield Mangum(module.app, lifespan="off")
    finally:
        if previous is None:
            os.environ.pop("SERVE_FRONTEND", None)
        else:
            os.environ["SERVE_FRONTEND"] = previous
        get_settings.cache_clear()


def test_the_landing_page_is_served_as_text(serving_handler: Any) -> None:
    response = serving_handler(event("GET", "/"), None)
    assert response["statusCode"] == 200
    assert "text/html" in response["headers"]["content-type"]
    # Text, not base64: Mangum only base64s what is not a text type, and getting this wrong shows up as
    # a page of gibberish rather than an error.
    assert response["isBase64Encoded"] is False
    assert "<html" in body_of(response).lower()


def test_every_book_path_returns_the_one_funnel_shell(serving_handler: Any) -> None:
    """Refresh and pasted links have to work on a deep funnel URL, and on this deploy this route is the
    only rewrite there is: no CDN sits in front holding its own copy of the rule."""
    shell = body_of(serving_handler(event("GET", "/book"), None))
    for path in ("/book/date", "/book/package/plus", "/book/confirm"):
        response = serving_handler(event("GET", path), None)
        assert response["statusCode"] == 200, path
        assert "text/html" in response["headers"]["content-type"], path
        assert body_of(response) == shell, path


def test_the_stylesheet_and_scripts_are_served_as_text(serving_handler: Any) -> None:
    for path, fragment in (("/css/encore.css", "--blue"), ("/js/book.js", "")):
        response = serving_handler(event("GET", path), None)
        assert response["statusCode"] == 200, path
        assert response["isBase64Encoded"] is False, path
        assert fragment in body_of(response), path


def test_a_missing_asset_is_a_404_not_the_shell(serving_handler: Any) -> None:
    """The SPA catch-all is scoped to /book/*. A typo'd script path must fail loudly rather than return
    HTML that the browser then tries to execute as JavaScript."""
    response = serving_handler(event("GET", "/js/does-not-exist.js"), None)
    assert response["statusCode"] == 404
