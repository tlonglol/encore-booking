import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mangum import Mangum

from app.config import get_settings
from app.routes import admin, availability, bookings, dev, holds, quote, requests, waitlist, webhooks

# The console fallback for SMS logs at INFO; uvicorn leaves application loggers at WARNING otherwise.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Encore Party Booking")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(quote.router)
app.include_router(availability.router)
app.include_router(holds.router)
app.include_router(requests.router)
app.include_router(waitlist.router)
app.include_router(bookings.router)
app.include_router(webhooks.router)
app.include_router(dev.router)
app.include_router(admin.router)

if get_settings().serve_frontend and FRONTEND_DIR.is_dir():
    # The funnel is a single-page app: every /book/* path gets the same shell, so refresh and pasted links work.
    # On CloudFront the same rewrite has to be configured there.
    @app.get("/book")
    @app.get("/book/{step:path}")
    def book_shell(step: str = "") -> FileResponse:
        return FileResponse(FRONTEND_DIR / "book.html")

    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

handler = Mangum(app)
