import os
from abc import ABC, abstractmethod


class _SMSProvider(ABC):
    @abstractmethod
    def send(self, numero: str, message: str) -> None:
        pass


class _TwilioProvider(_SMSProvider):
    def __init__(self):
        from twilio.rest import Client
        self._client = Client(
            os.getenv("SMS_API_KEY"),
            os.getenv("SMS_API_SECRET"),
        )
        self._from = os.getenv("SMS_SENDER")

    def send(self, numero: str, message: str) -> None:
        self._client.messages.create(body=message, from_=self._from, to=numero)


class _BrevoSMSProvider(_SMSProvider):
    def __init__(self):
        self._api_key = os.getenv("SMS_API_KEY") or os.getenv("BREVO_API_KEY")
        self._sender = os.getenv("SMS_SENDER", "Develly")

    def send(self, numero: str, message: str) -> None:
        import httpx

        numero_clean = numero.replace(" ", "").replace("-", "").replace(".", "")
        if not numero_clean.startswith("+"):
            numero_clean = f"+{numero_clean}"

        url = "https://api.brevo.com/v3/transactionalSMS/sms"
        headers = {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "sender": self._sender,
            "recipient": numero_clean,
            "content": message,
        }
        with httpx.Client(headers=headers, timeout=30) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()


_PROVIDERS: dict[str, type[_SMSProvider]] = {
    "twilio": _TwilioProvider,
    "brevo": _BrevoSMSProvider,
}


class SmsService:
    def __init__(self):
        provider_name = (os.getenv("SMS_PROVIDER") or "twilio").lower()
        if provider_name not in _PROVIDERS:
            raise ValueError(
                f"SMS provider '{provider_name}' not supported. "
                f"Available: {list(_PROVIDERS.keys())}"
            )
        self._provider = _PROVIDERS[provider_name]()

    def send_sms(self, numero: str, message: str) -> None:
        truncated = message[:160]
        self._provider.send(numero, truncated)
