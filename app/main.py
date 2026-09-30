from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from mangum import Mangum

from app.config import get_settings
from app.routes import admin, availability, bookings, dev, holds, quote, webhooks

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
app.include_router(bookings.router)
app.include_router(webhooks.router)
app.include_router(dev.router)
app.include_router(admin.router)

if get_settings().serve_frontend and FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

handler = Mangum(app)
