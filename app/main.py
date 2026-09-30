from fastapi import FastAPI
from mangum import Mangum

from app.routes import quote

app = FastAPI(title="Encore Party Booking")
app.include_router(quote.router)

handler = Mangum(app)
