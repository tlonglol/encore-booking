import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Hold
from app.notifications import notify_after_commit
from app.services.booking import handle_square_webhook
from app.services.square import signature_for

router = APIRouter(prefix="/api/dev")


@router.post("/fake-payment/{hold_id}")
def fake_payment(hold_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, str]:
    """Local demo without Square credentials: builds a signed payment.updated event and runs the real webhook path."""
    settings = get_settings()
    if not settings.fake_square_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    hold = db.get(Hold, hold_id)
    if hold is None or not hold.square_order_id:
        raise HTTPException(status_code=404, detail="Hold not found")
    payment_id = f"dev-pay-{uuid.uuid4()}"
    raw = json.dumps(
        {
            "type": "payment.updated",
            "event_id": str(uuid.uuid4()),
            "data": {
                "type": "payment",
                "id": payment_id,
                "object": {
                    "payment": {
                        "id": payment_id,
                        "status": "COMPLETED",
                        "amount_money": {"amount": hold.deposit_cents, "currency": "USD"},
                        "order_id": hold.square_order_id,
                    }
                },
            },
        }
    ).encode()
    signature = signature_for(raw, settings.square_webhook_signature_key, settings.square_webhook_notification_url)
    db.rollback()
    result = handle_square_webhook(db, settings, raw, signature)
    notify_after_commit(db, settings, result)
    if result.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Fake webhook rejected: {result.outcome}")
    return {"outcome": result.outcome}
