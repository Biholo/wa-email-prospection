import os
import re
import time

import httpx

WASENDER_API_BASE = "https://www.wasenderapi.com/api"


class WasenderService:
    def __init__(self):
        self.api_key = os.getenv("WASENDER_API_KEY")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _normalize(self, numero: str) -> str:
        digits = re.sub(r"\D", "", numero)
        return f"+{digits}"

    def _is_session_error(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in (401, 403)
        return False

    def _notify_disconnect(self) -> None:
        try:
            from services.resend_service import ResendService
            ResendService().send_email(
                to="kilian.trouet@gmail.com",
                subject="⚠️ Develly: WaSender déconnecté",
                html=(
                    "<p>La session WaSender s'est déconnectée.</p>"
                    "<p>Reconnectez-vous sur "
                    "<a href='https://www.wasenderapi.com'>wasenderapi.com</a>.</p>"
                ),
            )
            print("[Wasender] Notification disconnect envoyée → kilian.trouet@gmail.com")
        except Exception as e:
            print(f"[Wasender] Erreur envoi notification disconnect : {e}")

    def check_whatsapp(self, numero: str) -> bool:
        normalized = self._normalize(numero)
        url = f"{WASENDER_API_BASE}/on-whatsapp/{normalized}"
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(headers=self.headers, timeout=15) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    exists = bool(data.get("data", {}).get("exists", False))
                    print(f"       [Wasender] check {normalized} → {'OUI' if exists else 'NON'} | {data}")
                    return exists
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if self._is_session_error(exc):
                    self._notify_disconnect()
                if exc.response.status_code == 422:
                    print(f"       [Wasender] check {normalized} → NON (422 numéro invalide)")
                    return False
                if attempt < 2:
                    wait = 5 * (attempt + 1)  # 5s, 10s
                    print(f"       [Wasender] check {normalized} → 408 retry {attempt + 1}/3 (attente {wait}s)")
                    time.sleep(wait)
                    continue
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
        print(f"       [Wasender] check {normalized} → ERREUR : {last_exc}")
        raise last_exc

    def add_contact(self, numero: str, full_name: str) -> bool:
        url = f"{WASENDER_API_BASE}/contacts"
        digits = re.sub(r"\D", "", numero)
        jid = f"{digits}@s.whatsapp.net"
        payload = {"jid": jid, "fullName": full_name, "saveOnPrimaryAddressbook": False}
        try:
            with httpx.Client(headers=self.headers, timeout=15) as client:
                resp = client.put(url, json=payload)
                resp.raise_for_status()
                print(f"       [Wasender] contact ajouté : {jid} / {full_name}")
                return True
        except Exception as exc:
            if self._is_session_error(exc):
                self._notify_disconnect()
            print(f"       [Wasender] add_contact {jid} → ERREUR : {exc}")
            return False

    def send_whatsapp(self, numero: str, message: str):
        url = f"{WASENDER_API_BASE}/send-message"
        payload = {"to": self._normalize(numero), "text": message}
        try:
            with httpx.Client(headers=self.headers, timeout=30) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            if self._is_session_error(exc):
                self._notify_disconnect()
            raise

    def send_group_whatsapp(self, group_id: str, message: str):
        url = f"{WASENDER_API_BASE}/send-message"
        payload = {"to": group_id, "text": message}
        try:
            with httpx.Client(headers=self.headers, timeout=30) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            if self._is_session_error(exc):
                print("[Wasender] ⚠️ Session expirée")
            raise

    def upload_file(self, file_bytes: bytes, content_type: str = "application/pdf") -> str:
        """Upload file to WaSender CDN. Returns public URL (valid 24h)."""
        url = f"{WASENDER_API_BASE}/upload"
        upload_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": content_type,
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=upload_headers, content=file_bytes)
            resp.raise_for_status()
            return resp.json()["publicUrl"]

    def send_document(self, numero: str, file_url: str, caption: str = "") -> dict:
        """Send a document (PDF) via WhatsApp."""
        url = f"{WASENDER_API_BASE}/send-message"
        payload = {
            "to": self._normalize(numero),
            "document": file_url,
            "caption": caption,
        }
        try:
            with httpx.Client(headers=self.headers, timeout=30) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            if self._is_session_error(exc):
                self._notify_disconnect()
            raise
