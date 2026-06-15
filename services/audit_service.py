import io
import unicodedata
from datetime import date
from pathlib import Path

import yaml
from pypdf import PdfReader, PdfWriter
from playwright.sync_api import sync_playwright

TEMPLATES_DIR = Path("templates/audit")
NICHE_CONFIG_DIR = Path("config/niches")
IMAGES_DIR = Path(__file__).parent.parent.parent / "slides_demarchage" / "images"

ANGLE_TO_AUDIT_TYPE: dict[str, str] = {
    "RÉPUTATION": "bad-gmb",
    "ESTHÉTISME": "bad-site",
    "INVISIBILITÉ": "no-site",
}


def _normalize_niche(label: str) -> str:
    from logic.competitor_resolver import _normalize_niche as _norm
    return _norm(label)


class AuditService:
    def __init__(self, supabase_service):
        self._supabase = supabase_service

    def load_niche_config(self, niche_key: str) -> dict:
        path = NICHE_CONFIG_DIR / f"{niche_key}.yaml"
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def build_variables(
        self,
        contact: dict,
        concurrent_data: dict,
        niche_config: dict,
        lead_data: dict | None = None,
    ) -> dict:
        attrs = contact.get("attributes", {})
        c1 = concurrent_data.get("concurrent_1", {})
        c2 = concurrent_data.get("concurrent_2", {})
        c3 = concurrent_data.get("concurrent_3", {})
        site_url = attrs.get("WEBSITE_URL", "") or ""
        niche_label = niche_config.get("label", attrs.get("CATEGORIE", ""))
        today_str = date.today().strftime("%d/%m/%Y")

        def str_val(v, default="-") -> str:
            return str(v) if v not in (None, "", 0) else default

        return {
            # Prospect
            "PROSPECT.company": attrs.get("COMPANY", ""),
            "PROSPECT.average_rate": str_val(attrs.get("AVERAGE_RATE")),
            "PROSPECT.number_of_rate": str_val(attrs.get("NUMBER_OF_RATE")),
            "PROSPECT.ville": concurrent_data.get("ville_nom", ""),
            "PROSPECT.photos_count": str_val((lead_data or {}).get("photos_count")),
            "PROSPECT.is_verified": "OUI" if (lead_data or {}).get("is_google_verified") else "NON",
            "PROSPECT.has_website": "OUI" if site_url else "NON",
            "PROSPECT.has_website_class": "status-yes" if site_url else "status-no",
            # Competitor 1
            "PROSPECT.concurrent_1_nom": c1.get("nom", "-"),
            "PROSPECT.concurrent_1_note": str_val(c1.get("note")),
            "PROSPECT.concurrent_1_avis": str_val(c1.get("nb_avis")),
            "PROSPECT.concurrent_top": c1.get("nom", "-"),
            "conc_1_note": str_val(c1.get("note")),
            "conc_1_avis": str_val(c1.get("nb_avis")),
            "conc_1_photos": str_val(c1.get("photos_count")),
            "CONCURRENT_1.has_website": "OUI" if c1.get("has_website") else "NON",
            "CONCURRENT_1.has_website_class": "status-yes" if c1.get("has_website") else "status-no",
            # Competitor 2
            "PROSPECT.concurrent_2_nom": c2.get("nom", "-"),
            "PROSPECT.concurrent_2_note": str_val(c2.get("note")),
            "PROSPECT.concurrent_2_avis": str_val(c2.get("nb_avis")),
            "conc_2_note": str_val(c2.get("note")),
            "conc_2_avis": str_val(c2.get("nb_avis")),
            "conc_2_photos": str_val(c2.get("photos_count")),
            # Competitor 3
            "PROSPECT.concurrent_3_nom": c3.get("nom", "-"),
            "PROSPECT.concurrent_3_note": str_val(c3.get("note")),
            "PROSPECT.concurrent_3_avis": str_val(c3.get("nb_avis")),
            "conc_3_note": str_val(c3.get("note")),
            "conc_3_avis": str_val(c3.get("nb_avis")),
            "conc_3_photos": str_val(c3.get("photos_count")),
            # Date & niche
            "DATE": today_str,
            "NICHE.label": niche_label,
            "NICHE.LABEL": niche_label.upper(),
            # bad-gmb niche vars
            "NICHE.gmb_contenu": niche_config.get("gmb_contenu", ""),
            "NICHE.valeur_gmb_bloc1": niche_config.get("valeur_gmb_bloc1", ""),
            "NICHE.valeur_gmb_bloc2": niche_config.get("valeur_gmb_bloc2", ""),
            "NICHE.valeur_gmb_bloc3": niche_config.get("valeur_gmb_bloc3", ""),
            # bad-site niche vars
            "NICHE.refonte_contenu": niche_config.get("refonte_contenu", ""),
            "NICHE.valeur_bloc1": niche_config.get("valeur_bloc1", ""),
            "NICHE.valeur_bloc2": niche_config.get("valeur_bloc2", ""),
            "NICHE.valeur_bloc3": niche_config.get("valeur_bloc3", ""),
            # no-site niche vars
            "NICHE.site_contenu": niche_config.get("site_contenu", ""),
        }

    def _inject(self, html: str, variables: dict) -> str:
        for key, value in variables.items():
            html = html.replace(f"{{{{{key}}}}}", str(value))
        return html

    def render_slides_to_pdf(self, audit_type: str, variables: dict) -> bytes:
        template_dir = TEMPLATES_DIR / audit_type
        slides = sorted(template_dir.glob("slide_*.html"), key=lambda p: p.name)

        images_url = IMAGES_DIR.resolve().as_posix()
        slide_pdfs: list[bytes] = []

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 720})

            for slide_path in slides:
                html = slide_path.read_text(encoding="utf-8")
                abs_css = slide_path.parent.resolve() / "global.css"
                html = html.replace('href="global.css"', f'href="file:///{abs_css.as_posix()}"')
                html = html.replace('src="/images/', f'src="file:///{images_url}/')
                html = self._inject(html, variables)

                tmp = slide_path.parent / f"_tmp_{slide_path.name}"
                try:
                    tmp.write_text(html, encoding="utf-8")
                    page.goto(f"file:///{tmp.resolve().as_posix()}")
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(800)
                    slide_pdfs.append(page.pdf(
                        width="1280px",
                        height="720px",
                        print_background=True,
                    ))
                finally:
                    tmp.unlink(missing_ok=True)

            browser.close()

        writer = PdfWriter()
        for pdf_bytes in slide_pdfs:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for pdf_page in reader.pages:
                writer.add_page(pdf_page)

        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def generate_and_upload(
        self,
        contact: dict,
        concurrent_data: dict,
        niche_config: dict,
        audit_type: str,
        lead_data: dict | None = None,
    ) -> tuple[str, bytes]:
        """Render PDF for contact, upload to Supabase. Returns (public_url, pdf_bytes)."""
        variables = self.build_variables(contact, concurrent_data, niche_config, lead_data)
        pdf_bytes = self.render_slides_to_pdf(audit_type, variables)

        company = contact.get("attributes", {}).get("COMPANY", "audit")
        safe = unicodedata.normalize("NFD", company.lower())
        safe = "".join(c for c in safe if unicodedata.category(c) != "Mn")
        safe = safe.replace(" ", "_").replace("/", "_")
        filename = f"{date.today().isoformat()}_{safe}_{audit_type}.pdf"

        return self._supabase.upload_pdf(filename, pdf_bytes), pdf_bytes
