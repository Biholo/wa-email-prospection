import os
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from services.supabase_service import SupabaseService

router = APIRouter(tags=["webhook"])


@router.post("/api/webhook/resend")
async def resend_webhook(request: Request):
    token = request.query_params.get("token")
    expected = os.getenv("RESEND_WEBHOOK_TOKEN")
    if expected and (not token or not secrets.compare_digest(token, expected)):
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    body = await request.json()
    event_type: str = body.get("type", "")
    data: dict = body.get("data", {})
    resend_id: str | None = data.get("email_id")
    created_at: str = body.get("created_at") or datetime.now(timezone.utc).isoformat()

    print(f"[RESEND WEBHOOK] type={event_type} resend_id={resend_id}")

    if not resend_id or not event_type.startswith("email."):
        return {"ok": True}

    try:
        supa = SupabaseService()
    except RuntimeError:
        return {"ok": True}

    if event_type == "email.opened":
        supa.handle_email_opened(resend_id, created_at)

    elif event_type == "email.clicked":
        supa.handle_email_clicked(resend_id, created_at)

    elif event_type == "email.delivered":
        supa.update_email_event(resend_id, {
            "status": "delivered",
            "delivered_at": created_at,
        })

    elif event_type == "email.bounced":
        bounce = data.get("bounce", {})
        raw_type = bounce.get("type", "HardBounce")
        bounce_type = "soft" if "soft" in raw_type.lower() else "hard"
        supa.update_email_event(resend_id, {
            "status": "bounced",
            "bounced_at": created_at,
            "bounce_type": bounce_type,
        })

    elif event_type == "email.complained":
        supa.update_email_event(resend_id, {
            "status": "complained",
            "complained_at": created_at,
        })

    elif event_type == "email.unsubscribed":
        supa.update_email_event(resend_id, {
            "status": "unsubscribed",
            "unsubscribed_at": created_at,
        })

    elif event_type == "email.failed":
        supa.update_email_event(resend_id, {"status": "failed"})

    elif event_type == "email.sent":
        supa.update_email_event(resend_id, {"status": "sent"})

    elif event_type == "email.delivery_delayed":
        print(f"[RESEND WEBHOOK] Delivery delayed for resend_id={resend_id}  -  no action")

    return {"ok": True}
