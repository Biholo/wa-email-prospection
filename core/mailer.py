import os
from datetime import date

import httpx

_RECAP_TO = "kilian@develly.io"
_BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def _send(subject: str, body: str) -> None:
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        print("[Mailer] BREVO_API_KEY absent — recap non envoyé")
        return
    try:
        resp = httpx.post(
            _BREVO_URL,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json={
                "sender": {"name": "Develly Pipeline", "email": _RECAP_TO},
                "to": [{"email": _RECAP_TO}],
                "subject": subject,
                "textContent": body,
            },
            timeout=10,
        )
        if resp.status_code in (200, 201, 202):
            print(f"[Mailer] Recap envoyé → {_RECAP_TO}")
        else:
            print(f"[Mailer] Erreur recap : {resp.status_code} — {resp.text[:150]}")
    except Exception as exc:
        print(f"[Mailer] Exception : {exc}")


class RecapTracker:
    def __init__(self, pipeline: str, config: str, dry_run: bool) -> None:
        self.pipeline = pipeline
        self.config = config
        self.dry_run = dry_run
        self.envoyes = 0
        self.erreurs = 0
        self._details: list[str] = []

    def record(self, status: str, detail: str = "") -> None:
        if status in ("ENVOYÉ", "DRY_RUN", "DÉPLACÉ", "TEST"):
            self.envoyes += 1
            if detail:
                self._details.append(detail)
        elif status == "ERREUR":
            self.erreurs += 1

    def send(self, total_list: int = 0) -> None:
        today = date.today().isoformat()
        tag = " [DRY RUN]" if self.dry_run else ""
        subject = (
            f"[Pipeline {self.pipeline}{tag}] {self.config}"
            f" — {self.envoyes} envoyé(s) — {today}"
        )
        lines = [
            f"Pipeline {self.pipeline}{tag} — {self.config} — {today}",
            "=" * 55,
        ]
        if total_list:
            autres = max(0, total_list - self.envoyes - self.erreurs)
            lines += [
                f"Total liste  : {total_list}",
                f"Envoyés      : {self.envoyes}",
                f"Erreurs      : {self.erreurs}",
                f"Autres       : {autres}  (hors scope / skippés / limite atteinte)",
            ]
        else:
            lines += [
                f"Envoyés      : {self.envoyes}",
                f"Erreurs      : {self.erreurs}",
            ]
        lines += [
            "",
            "── Détail envois ─────────────────────────────────────",
        ]
        lines.extend(self._details if self._details else ["(aucun)"])
        try:
            _send(subject, "\n".join(lines))
        except Exception as exc:
            print(f"[Mailer] Erreur inattendue : {exc}")
