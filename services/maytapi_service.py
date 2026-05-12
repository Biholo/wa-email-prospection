import os
import httpx


class MaytapiService:
    def __init__(self):
        self.api_key = os.getenv("MAYTAPI_API_KEY")
        self.phone_id = os.getenv("MAYTAPI_PHONE_ID")
        self.product_id = os.getenv("MAYTAPI_PRODUCT_ID")
        self.base_url = (
            f"https://api.maytapi.com/api/{self.product_id}/{self.phone_id}"
        )
        self.headers = {
            "x-maytapi-key": self.api_key,
            "Content-Type": "application/json",
        }

    def _normalize_number(self, numero: str) -> str:
        cleaned = numero.replace("+", "").replace(" ", "").replace("-", "").replace(".", "")
        return cleaned

    def check_whatsapp(self, numero: str) -> bool:
        url = f"{self.base_url}/checkWhatsapp"
        payload = {"number": self._normalize_number(numero)}
        try:
            with httpx.Client(headers=self.headers, timeout=15) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return bool(data.get("data", {}).get("status", False))
        except Exception:
            return False

    def send_whatsapp(self, numero: str, message: str):
        url = f"{self.base_url}/sendMessage"
        payload = {
            "to_number": self._normalize_number(numero),
            "type": "text",
            "message": message,
        }
        with httpx.Client(headers=self.headers, timeout=30) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
