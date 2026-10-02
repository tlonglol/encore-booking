"""AWS Lambda entrypoint. Terraform sets the handler to `app.lambda_handler.handler`.

One Lambda serves everything: `/api/*`, the static frontend, and the `/book/*` SPA catch-all, on a
public Function URL. Nothing here is on the money path except the raw request body, which Mangum hands
to the webhook route unchanged so Square's signature still verifies over the exact bytes Square signed.
"""

from typing import Any

from mangum import Mangum

from app.main import app

# The keep-warm schedule sends this instead of an HTTP event.
WARMER_KEY = "warmer"

# lifespan="off": the app registers no startup or shutdown hooks (tests/test_lazy_expiry.py pins that),
# so running the ASGI lifespan protocol on every cold start would only cost time. Migrations are an
# Alembic run from the laptop, never something a request triggers.
_asgi = Mangum(app, lifespan="off")


def handler(event: Any, context: Any) -> Any:
    if isinstance(event, dict) and event.get(WARMER_KEY):
        # Answered before the app, the settings or the database are touched: the point of the ping is to
        # keep the container and its warm connection pool alive, not to exercise anything.
        return {"warmed": True}
    return _asgi(event, context)
