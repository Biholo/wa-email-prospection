from logic.angle_detector import AngleDetector
from logic.competitor_resolver import CompetitorResolver
from services.pipeline_service import ChannelAdapter, run_pipeline
from services.audit_service import AuditService
from services.brevo_service import BrevoService
from services.pagespeed_service import PageSpeedService
from services.resend_service import ResendService


_AUDIT_SUBJECT = "Votre audit personnalisé — {company}"

_AUDIT_HTML = """\
<p>Bonjour {firstname},</p>
<p>Je vous envoie en pièce jointe votre audit personnalisé.</p>
<p>Il analyse votre présence en ligne et la compare à celle de vos principaux concurrents.</p>
<p>N'hésitez pas à revenir vers moi si vous souhaitez en discuter.</p>
<p>Kilian — Develly</p>
"""

_RELANCE_SUBJECT = "Avez-vous eu le temps de regarder votre audit ?"

_RELANCE_HTML = """\
<p>Bonjour {firstname},</p>
<p>Je vous avais envoyé votre audit personnalisé il y a quelques jours.</p>
<p>Avez-vous eu le temps d'y jeter un coup d'œil ?</p>
<p>Je reste disponible si vous avez des questions.</p>
<p>Kilian — Develly</p>
"""


class EmailAdapter(ChannelAdapter):
    canal = "EMAIL"

    def __init__(self, resend: ResendService):
        self._resend = resend

    def send_relance(self, contact: dict, attrs: dict) -> None:
        email = contact.get("email", "") or attrs.get("EMAIL", "")
        if not email:
            raise ValueError("Email vide")
        firstname = attrs.get("FIRSTNAME", "") or ""
        company = attrs.get("COMPANY", "")
        self._resend.send_email(
            to=email,
            subject=_RELANCE_SUBJECT,
            html=_RELANCE_HTML.format(firstname=firstname or company),
        )

    def send_nouveau(self, contact, attrs, pdf_url, pdf_bytes, concurrent_data, angle):
        email = contact.get("email", "") or attrs.get("EMAIL", "")
        if not email:
            raise ValueError("Email vide")
        company = attrs.get("COMPANY", "")
        firstname = attrs.get("FIRSTNAME", "") or ""
        safe = company.lower().replace(" ", "_")
        self._resend.send_email_with_attachment(
            to=email,
            subject=_AUDIT_SUBJECT.format(company=company),
            html=_AUDIT_HTML.format(firstname=firstname or company),
            pdf_bytes=pdf_bytes,
            filename=f"audit_{safe}.pdf",
        )


def run_email_pipeline(
    config_name: str,
    config: dict,
    brevo: BrevoService,
    resend: ResendService,
    pagespeed: PageSpeedService,
    angle_detector: AngleDetector,
    competitor_resolver: CompetitorResolver,
    audit_service: AuditService,
    dry_run: bool = False,
) -> None:
    run_pipeline(
        adapter=EmailAdapter(resend),
        config_name=config_name,
        config=config,
        brevo=brevo,
        pagespeed=pagespeed,
        angle_detector=angle_detector,
        competitor_resolver=competitor_resolver,
        audit_service=audit_service,
        dry_run=dry_run,
    )
