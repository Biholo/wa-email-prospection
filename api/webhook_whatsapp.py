import os
import secrets

from fastapi import APIRouter, Header, HTTPException, Request

from core.db import find_contact_by_jid, log_entry
from services.brevo_service import BrevoService

router = APIRouter(tags=["webhook"])

_WA_LIST_ID = 22
_WA_TEST_LIST_ID = 28
_WA_READ_TARGET = 24

_STATUS_MAP = {
    2: None,           # resolved dynamically (j0_sent vs j3_sent)
    3: "j0_delivered",
    4: "j0_read",
}


def _phone(jid: str) -> str:
    return jid.split("@")[0]


def _find_contact(brevo: BrevoService, phone: str, *list_ids: int) -> dict | None:
    phones_to_try = [phone]
    if not phone.startswith("+"):
        phones_to_try.append("+" + phone)
    print(f"[WA FIND] phone={phone!r} → essai formats: {phones_to_try} dans listes={list_ids}")
    for lid in list_ids:
        for p in phones_to_try:
            hits = brevo.get_contacts(liste_id=lid, attr_equals={"SMS": p}, limit=1)
            print(f"[WA FIND]   liste #{lid} SMS={p!r} → {len(hits)} résultat(s)")
            if hits:
                contact = hits[0]
                print(f"[WA FIND]   trouvé: email={contact.get('email')} listIds={contact.get('listIds', [])}")
                return contact
    print(f"[WA FIND] introuvable dans toutes les listes")
    return None


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
):
    secret = os.getenv("WASENDER_WEBHOOK_SECRET")
    if secret:
        if not x_webhook_signature or not secrets.compare_digest(x_webhook_signature, secret):
            raise HTTPException(status_code=401, detail="Invalid signature")

    body = await request.json()
    event = body.get("event", "")

    # ── Delivery / read status updates ──────────────────────────────────────
    if event == "messages.update":
        raw_data = body.get("data", {})
        data = raw_data[0] if isinstance(raw_data, list) and raw_data else raw_data
        print(f"[WA UPDATE] data_type={type(raw_data).__name__} data={data}")

        key = data.get("key", {})
        from_me = key.get("fromMe", False)
        status_code = data.get("status")
        raw_jid = key.get("remoteJid", "") or data.get("remoteJid", "")
        print(f"[WA UPDATE] fromMe={from_me} status_code={status_code} jid={raw_jid!r}")

        if not from_me:
            print(f"[WA UPDATE] fromMe=False — ignoré")
            return {"ok": True}

        if status_code not in _STATUS_MAP:
            print(f"[WA UPDATE] status_code={status_code} hors _STATUS_MAP — ignoré")
            return {"ok": True}

        phone = _phone(raw_jid)
        if not phone:
            return {"ok": True}

        msg_record = find_contact_by_jid(phone)
        if not msg_record:
            print(f"[WA UPDATE] jid={phone!r} introuvable en DB — ignoré")
            return {"ok": True}

        email = msg_record["contact_email"]
        wa_step = msg_record.get("wa_step", "wa_1")

        if status_code == 2:
            wa_status = "j3_sent" if wa_step == "wa_2" else "j0_sent"
        else:
            wa_status = _STATUS_MAP[status_code]

        brevo = BrevoService()
        print(f"[WA STATUS] update_contact {email!r} WA_STATUS={wa_status!r} …")
        brevo.update_contact(email, {"WA_STATUS": wa_status})
        log_entry({"canal": "WHATSAPP_STATUS", "contact_email": email, "status": wa_status,
                   "erreur_detail": f"status_code={status_code}"})
        print(f"[WA STATUS] {email}: {wa_status} OK")

        if status_code == 4:
            brevo_contact = _find_contact(brevo, phone, _WA_LIST_ID, _WA_TEST_LIST_ID)
            if brevo_contact:
                contact_lists = brevo_contact.get("listIds", [])
                for src in (_WA_LIST_ID, _WA_TEST_LIST_ID):
                    if src in contact_lists:
                        brevo.move_to_list(email, src, _WA_READ_TARGET)
                        print(f"[WA READ] {email}: liste {src} → {_WA_READ_TARGET}")
                        break

    # ── Contact replied to our message ──────────────────────────────────────
    elif event == "messages-personal.received":
        msg = body.get("data", {}).get("messages", {})
        key = msg.get("key", {})
        if key.get("fromMe", False):
            return {"ok": True}

        sender_jid = key.get("remoteJid", "")
        phone = _phone(sender_jid)
        text = msg.get("messageBody", "")

        brevo = BrevoService()
        contact = _find_contact(brevo, phone, _WA_LIST_ID, _WA_TEST_LIST_ID, _WA_READ_TARGET)
        if contact:
            email = contact.get("email", "")
            print(f"[WA REPLIED] update_contact {email!r} WA_STATUS=replied …")
            try:
                brevo.update_contact(email, {"WA_STATUS": "replied"})
                print(f"[WA REPLIED] update_contact OK")
            except Exception as exc:
                print(f"[WA REPLIED] update_contact ERREUR: {exc}")
            contact_lists = contact.get("listIds", [])
            print(f"[WA REPLIED] listIds du contact: {contact_lists}")
            for src in (_WA_LIST_ID, _WA_TEST_LIST_ID):
                if src in contact_lists:
                    print(f"[WA REPLIED] move_to_list {email!r} {src} → {_WA_READ_TARGET} …")
                    try:
                        brevo.move_to_list(email, src, _WA_READ_TARGET)
                        print(f"[WA REPLIED] move_to_list OK")
                    except Exception as exc:
                        print(f"[WA REPLIED] move_to_list ERREUR: {exc}")
                    break
            else:
                print(f"[WA REPLIED] {email}: déjà dans liste {_WA_READ_TARGET} ou liste inconnue — pas de move")
        else:
            print(f"[WA REPLIED] aucun contact trouvé pour phone={phone!r}")

        log_entry({
            "canal": "WHATSAPP_REPLIED",
            "contact_email": phone,
            "status": "replied",
            "erreur_detail": text[:500],
        })
        print(f"[WA INBOUND] {phone}: {text[:120]}")

    # ── Session status changes ───────────────────────────────────────────────
    elif event == "session.status":
        status = body.get("data", {}).get("status", "")
        session_id = body.get("sessionId", "")
        print(f"[WA SESSION] status={status} session={session_id}")

        if status in ("disconnected", "need_scan"):
            label = "déconnectée" if status == "disconnected" else "scan QR requis"
            try:
                from services.resend_service import ResendService
                ResendService().send_email(
                    to="kilian.trouet@gmail.com",
                    subject=f"⚠️ Develly: WaSender {label}",
                    html=(
                        f"<p>Session WaSender <strong>{label}</strong>.</p>"
                        f"<p>Session ID : <code>{session_id}</code></p>"
                        "<p>Reconnectez-vous sur "
                        "<a href='https://www.wasenderapi.com'>wasenderapi.com</a>.</p>"
                    ),
                )
                print(f"[WA SESSION] Email envoyé → kilian.trouet@gmail.com ({status})")
            except Exception as exc:
                print(f"[WA SESSION] Erreur envoi email : {exc}")

        log_entry({
            "canal": "WHATSAPP_SESSION",
            "contact_email": "",
            "status": status,
            "erreur_detail": f"sessionId={session_id}",
        })

    # ── Generic inbound (non-campaign messages) ──────────────────────────────
    elif event == "messages.received":
        msg = body.get("data", {}).get("messages", {})
        key = msg.get("key", {})
        from_me = key.get("fromMe", False)
        sender = key.get("cleanedSenderPn", "") or key.get("remoteJid", "")
        text = msg.get("messageBody", "")

        if not from_me:
            phone = key.get("cleanedSenderPn", "") or _phone(key.get("remoteJid", ""))
            brevo = BrevoService()
            contact = _find_contact(brevo, phone, _WA_LIST_ID, _WA_TEST_LIST_ID, _WA_READ_TARGET)
            if contact:
                email = contact.get("email", "")
                print(f"[WA REPLIED] update_contact {email!r} WA_STATUS=replied …")
                try:
                    brevo.update_contact(email, {"WA_STATUS": "replied"})
                    print(f"[WA REPLIED] update_contact OK")
                except Exception as exc:
                    print(f"[WA REPLIED] update_contact ERREUR: {exc}")
                contact_lists = contact.get("listIds", [])
                print(f"[WA REPLIED] listIds du contact: {contact_lists}")
                for src in (_WA_LIST_ID, _WA_TEST_LIST_ID):
                    if src in contact_lists:
                        print(f"[WA REPLIED] move_to_list {email!r} {src} → {_WA_READ_TARGET} …")
                        try:
                            brevo.move_to_list(email, src, _WA_READ_TARGET)
                            print(f"[WA REPLIED] move_to_list OK")
                        except Exception as exc:
                            print(f"[WA REPLIED] move_to_list ERREUR: {exc}")
                        break
                else:
                    print(f"[WA REPLIED] {email}: déjà dans liste {_WA_READ_TARGET} ou liste inconnue — pas de move")
            else:
                print(f"[WA REPLIED] aucun contact trouvé pour phone={phone!r}")

            log_entry({
                "canal": "WHATSAPP_INBOUND",
                "contact_email": sender,
                "status": "RECU",
                "erreur_detail": text[:500],
            })
            print(f"[WA INBOUND] {sender}: {text[:120]}")

    return {"ok": True}
