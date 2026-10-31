import logging
from pathlib import Path

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.services.sessions import (
    COOKIE_MAX_AGE as SESSION_MAX_AGE,
    COOKIE_NAME as SESSION_COOKIE,
    clean_session_id,
    new_session_id,
)
from app.routes import (
    admin,
    availability,
    bookings,
    config,
    holds,
    manage,
    quote,
    requests,
    waitlist,
    webhooks,
)

# The console fallback for SMS logs at INFO; uvicorn leaves application loggers at WARNING otherwise.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Encore Party Booking")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    # PATCH is on the money path: the funnel saves the email on blur and mirrors the draft with it.
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def anonymous_session(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Gives every browser an anonymous id, so the server can tell two hold requests from one browser apart
    from two browsers. Set once and refreshed never: the cookie identifies a browser and nothing else."""
    existing = clean_session_id(request.cookies.get(SESSION_COOKIE))
    request.state.session_id = existing or new_session_id()
    response = await call_next(request)
    if existing is None:
        response.set_cookie(
            SESSION_COOKIE,
            request.state.session_id,
            max_age=SESSION_MAX_AGE,
            path="/",
            httponly=True,
            secure=True,
            # The frontend is deployed to a different origin than the API, so the cookie has to survive a
            # cross-site XHR. SameSite=None requires Secure, which browsers allow on localhost for dev.
            samesite="none",
        )
    return response


app.include_router(config.router)
app.include_router(quote.router)
app.include_router(availability.router)
app.include_router(holds.router)
app.include_router(requests.router)
app.include_router(waitlist.router)
app.include_router(bookings.router)
app.include_router(manage.router)
app.include_router(webhooks.router)
app.include_router(admin.router)

class FrontendFiles(StaticFiles):
    """Cache JS, CSS and images for 5 minutes. HTML always revalidates.

    Every static file here is a Lambda call, so a cold load of book.html is 12 requests at once.
    With caching, a repeat visit is one.

    Only 5 minutes because the filenames have no content hash, so after a deploy the old book.js
    just has to age out.
    """

    HELD = (".js", ".css", ".svg", ".woff", ".woff2", ".png", ".jpg", ".jpeg", ".webp", ".ico")

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "max-age=300" if path.endswith(self.HELD) else "no-cache"
        return response


if get_settings().serve_frontend and FRONTEND_DIR.is_dir():
    # The funnel is a single-page app: every /book/* path gets the same shell, so refresh and pasted links
    # work. This route is the rewrite; the deploy is one origin, so there is no CDN that needs its own copy.
    @app.get("/book")
    @app.get("/book/{step:path}")
    def book_shell(step: str = "") -> FileResponse:
        return FileResponse(FRONTEND_DIR / "book.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", FrontendFiles(directory=FRONTEND_DIR, html=True), name="frontend")
