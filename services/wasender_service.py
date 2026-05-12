import os
import re

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

    def check_whatsapp(self, numero: str) -> bool:
        normalized = self._normalize(numero)
        url = f"{WASENDER_API_BASE}/on-whatsapp/{normalized}"
        try:
            with httpx.Client(headers=self.headers, timeout=15) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
                exists = bool(data.get("data", {}).get("exists", False))
                print(f"       [Wasender] check {normalized} → {'OUI' if exists else 'NON'} | {data}")
                return exists
        except Exception as exc:
            print(f"       [Wasender] check {normalized} → ERREUR : {exc}")
            return False

    def send_whatsapp(self, numero: str, message: str):
        url = f"{WASENDER_API_BASE}/send-message"
        payload = {"to": self._normalize(numero), "text": message}
        with httpx.Client(headers=self.headers, timeout=30) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
