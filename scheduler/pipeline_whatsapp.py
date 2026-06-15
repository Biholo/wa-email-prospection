import random
import time

from core.db import save_wa_message
from logic.angle_detector import AngleDetector
from logic.competitor_resolver import CompetitorResolver
from services.pipeline_service import ChannelAdapter, run_pipeline
from services.audit_service import AuditService
from services.brevo_service import BrevoService
from services.pagespeed_service import PageSpeedService
from services.wasender_service import WasenderService


TEST_LIST_ID = 28

_RELANCE_WA = (
    "Bonjour {firstname} 👋\n\n"
    "Je vous avais envoyé votre audit personnalisé il y a quelques jours.\n\n"
    "Avez-vous eu le temps d'y jeter un coup d'œil ?\n\n"
    "Kilian — Develly"
)

_AUDIT_CAPTION = (
    "Bonjour {firstname} 👋\n\n"
    "Je vous envoie votre audit personnalisé — il analyse votre présence en ligne "
    "et la compare à celle de vos principaux concurrents.\n\n"
    "Kilian — Develly"
)


class WhatsAppAdapter(ChannelAdapter):
    canal = "WHATSAPP"

    def __init__(self, wasender: WasenderService, test_phone: str = ""):
        self._wasender = wasender
        self._test_phone = test_phone

    @property
    def is_test_send(self) -> bool:
        return bool(self._test_phone)

    def _sms(self, attrs: dict) -> str:
        """Retourne test_phone si défini, sinon numéro réel du contact."""
        return self._test_phone or attrs.get("SMS", "") or ""

    def nouveaux_extra_null_filter(self) -> dict:
        return {"WA_STATUS": True}

    def pre_nouveau_check(self, contact, attrs, brevo, source_list_id, today_str, log_record, tag) -> bool:
        if self._test_phone:
            # Bypass check WA — test_phone supposé valide
            log_record["whatsapp_check"] = "OUI (TEST)"
            return True

        sms = attrs.get("SMS", "") or ""
        has_whatsapp = self._wasender.check_whatsapp(sms) if sms else False
        log_record["whatsapp_check"] = "OUI" if has_whatsapp else "NON"
        if not has_whatsapp:
            email = contact.get("email", "")
            company = attrs.get("COMPANY", "")
            brevo.update_contact(
                email,
                {"CANAL_PRINCIPAL": "SMS", "COMMENTAIRE": f"Bascule SMS détectée le {today_str}"},
            )
            brevo.move_to_list(email, source_list_id, 26)
            print(f"{tag}  {company} ({email}) → WORKFLOW SMS (pas WhatsApp)")
            log_record.update(status="WORKFLOW SMS", erreur_detail="Pas WhatsApp — bascule SMS")
            return False
        return True

    def send_relance(self, contact: dict, attrs: dict) -> None:
        sms = self._sms(attrs)
        if not sms:
            raise ValueError("Champ SMS vide — impossible d'envoyer WhatsApp")
        firstname = attrs.get("FIRSTNAME", "") or ""
        company = attrs.get("COMPANY", "")
        self._wasender.send_whatsapp(sms, _RELANCE_WA.format(firstname=firstname or company))

    def after_relance_sent(self, contact: dict, attrs: dict) -> None:
        save_wa_message(self._sms(attrs), contact.get("email", "") or "", "relance")

    def send_nouveau(self, contact, attrs, pdf_url, pdf_bytes, concurrent_data, angle):
        sms = self._sms(attrs)
        if not sms:
            raise ValueError("Champ SMS vide — impossible d'envoyer WhatsApp")
        firstname = attrs.get("FIRSTNAME", "") or ""
        lastname = attrs.get("LASTNAME", "") or ""
        company = attrs.get("COMPANY", "")

        wa_file_url = self._wasender.upload_file(pdf_bytes)
        caption = _AUDIT_CAPTION.format(firstname=firstname or company)

        if self._test_phone:
            # En test : skip add_contact + sleep (déjà dans les contacts)
            self._wasender.send_document(sms, wa_file_url, caption=caption)
        else:
            contact_name = f"{firstname} {lastname}".strip() or company
            self._wasender.add_contact(sms, contact_name)
            wait_sec = random.randint(300, 600)
            print(f"  {company} — contact ajouté, attente {wait_sec}s…")
            time.sleep(wait_sec)
            self._wasender.send_document(sms, wa_file_url, caption=caption)

    def after_nouveau_sent(self, contact: dict, attrs: dict) -> None:
        save_wa_message(self._sms(attrs), contact.get("email", "") or "", "audit_wa")


def run_whatsapp_pipeline(
    config_name: str,
    config: dict,
    brevo: BrevoService,
    wasender: WasenderService,
    pagespeed: PageSpeedService,
    angle_detector: AngleDetector,
    competitor_resolver: CompetitorResolver,
    audit_service: AuditService,
    dry_run: bool = False,
    mode: str = "all",
    test_phone: str = "",
    test_mode: bool = False,
) -> None:
    source_list_id_override = TEST_LIST_ID if test_mode else None
    tag_override = "[TEST] " if (test_mode or test_phone) else ""

    run_pipeline(
        adapter=WhatsAppAdapter(wasender, test_phone=test_phone),
        config_name=config_name,
        config=config,
        brevo=brevo,
        pagespeed=pagespeed,
        angle_detector=angle_detector,
        competitor_resolver=competitor_resolver,
        audit_service=audit_service,
        dry_run=dry_run,
        mode=mode,
        source_list_id_override=source_list_id_override,
        tag_override=tag_override,
    )
