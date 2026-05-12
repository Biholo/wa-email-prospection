import os

import httpx

RESEND_API_BASE = "https://api.resend.com"


class ResendService:
    def __init__(self):
        self.api_key = os.getenv("RESEND_API_KEY")
        self.from_email = os.getenv("RESEND_FROM_EMAIL", "Develly <notifications@develly.fr>")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def send_email(self, to: str, subject: str, html: str) -> dict:
        payload = {
            "from": self.from_email,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        with httpx.Client(headers=self.headers, timeout=15) as client:
            resp = client.post(f"{RESEND_API_BASE}/emails", json=payload)
            resp.raise_for_status()
            return resp.json()
