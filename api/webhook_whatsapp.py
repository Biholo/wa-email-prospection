from fastapi import APIRouter, Request

from core.db import log_entry

router = APIRouter(tags=["webhook"])


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    body = await request.json()
    event = body.get("event", "")

    if event == "messages.received":
        msg = body.get("data", {}).get("messages", {})
        key = msg.get("key", {})
        from_me = key.get("fromMe", False)
        sender = key.get("cleanedSenderPn", "") or key.get("remoteJid", "")
        text = msg.get("messageBody", "")

        if not from_me:
            log_entry({
                "canal": "WHATSAPP_INBOUND",
                "contact_email": sender,
                "status": "RECU",
                "erreur_detail": text[:500],
            })
            print(f"[WA INBOUND] {sender}: {text[:120]}")

    return {"ok": True}
