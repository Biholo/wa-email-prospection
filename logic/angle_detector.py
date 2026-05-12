import math
from datetime import datetime

import httpx
import whois as whois_mod
from bs4 import BeautifulSoup


class AngleDetector:
    def __init__(self, config: dict):
        self.priorite: list[str] = config.get("rules", {}).get(
            "angle_priority",
            ["INVISIBILITÉ", "RÉPUTATION", "ESTHÉTISME"],
        )

    def detect(
        self,
        contact: dict,
        pagespeed_score: int | None,
        reputation_candidates: list[dict] | None = None,
        exclude_angles: list[str] | None = None,
    ) -> str | None:
        attrs = contact.get("attributes", {})
        excluded = set(exclude_angles or [])
        candidates = reputation_candidates or []

        for angle in self.priorite:
            if angle in excluded:
                print(f"       [Angle] {angle} → exclu")
                continue

            if angle == "INVISIBILITÉ":
                site = attrs.get("WEBSITE_URL") or ""
                if not site.strip():
                    print(f"       [Angle] INVISIBILITÉ → DÉCLENCHÉ (pas de site)")
                    return "INVISIBILITÉ"
                print(f"       [Angle] INVISIBILITÉ → skip (site: {site.strip()})")

            elif angle == "RÉPUTATION":
                note_raw = attrs.get("AVERAGE_RATE")
                if note_raw in (None, ""):
                    print(f"       [Angle] RÉPUTATION → skip (note absente)")
                else:
                    try:
                        lead_note = float(note_raw)
                        triggered, reason = self._can_trigger_reputation(lead_note, candidates)
                        if triggered:
                            print(f"       [Angle] RÉPUTATION → DÉCLENCHÉ ({reason})")
                            return "RÉPUTATION"
                        print(f"       [Angle] RÉPUTATION → skip ({reason})")
                    except (ValueError, TypeError):
                        print(f"       [Angle] RÉPUTATION → skip (note invalide: {note_raw!r})")

            elif angle == "ESTHÉTISME":
                site = attrs.get("WEBSITE_URL") or ""
                if not site.strip():
                    print(f"       [Angle] ESTHÉTISME → skip (pas de site)")
                else:
                    score = self._score_esthetisme(site.strip(), pagespeed_score)
                    if score < 50:
                        print(f"       [Angle] ESTHÉTISME → DÉCLENCHÉ (score {score} < 50)")
                        return "ESTHÉTISME"
                    print(f"       [Angle] ESTHÉTISME → skip (score {score} >= 50)")

        print(f"       [Angle] aucun angle déclenché")
        return None

    def explain_skip(
        self,
        contact: dict,
        pagespeed_score: int | None,
        reputation_candidates: list[dict] | None = None,
        exclude_angles: list[str] | None = None,
    ) -> str:
        attrs = contact.get("attributes", {})
        excluded = set(exclude_angles or [])
        candidates = reputation_candidates or []
        parts: list[str] = []

        for angle in self.priorite:
            if angle in excluded:
                parts.append(f"{angle}: exclu")
                continue

            if angle == "INVISIBILITÉ":
                site = attrs.get("WEBSITE_URL") or ""
                if site.strip():
                    parts.append("INVISIBILITÉ: a un site")
                else:
                    parts.append("INVISIBILITÉ: ?")

            elif angle == "RÉPUTATION":
                note_raw = attrs.get("AVERAGE_RATE")
                if note_raw in (None, ""):
                    parts.append("RÉPUTATION: note absente")
                else:
                    try:
                        lead_note = float(note_raw)
                        _, reason = self._can_trigger_reputation(lead_note, candidates)
                        parts.append(f"RÉPUTATION: {reason}")
                    except (ValueError, TypeError):
                        parts.append("RÉPUTATION: note invalide")

            elif angle == "ESTHÉTISME":
                site = attrs.get("WEBSITE_URL") or ""
                if not site.strip():
                    parts.append("ESTHÉTISME: pas de site")
                else:
                    score = self._score_esthetisme(site.strip(), pagespeed_score)
                    parts.append(f"ESTHÉTISME: score {score} >= 50")

        return " | ".join(parts) if parts else "aucun angle évalué"

    # ── Réputation ───────────────────────────────────────────────

    @staticmethod
    def _can_trigger_reputation(lead_note: float, competitors: list[dict]) -> tuple[bool, str]:
        if not lead_note or lead_note == 0:
            return False, "note=0"
        if lead_note >= 4.5:
            return False, f"note {lead_note} >= 4.5"

        better_rated = [c for c in competitors if float(c.get("note") or 0) > lead_note]
        if not better_rated:
            return False, "aucun concurrent mieux noté"

        best = max(better_rated, key=AngleDetector._score_competitor)
        gap = float(best.get("note") or 0) - lead_note
        if gap < 0.3:
            return False, f"gap {gap:.2f} < 0.3 (meilleur: {best.get('nom', '?')})"

        return True, f"note {lead_note} vs {best.get('note')} ({best.get('nom', '?')}) gap={gap:.2f}"

    @staticmethod
    def _score_competitor(c: dict) -> float:
        note = float(c.get("note") or 0)
        nb_avis = int(c.get("nb_avis") or 0)
        if note == 0:
            return 0.0
        if nb_avis == 0:
            return note
        return note * math.log(nb_avis + 1)

    # ── Esthétisme scoring ───────────────────────────────────────

    def _score_esthetisme(self, url: str, pagespeed_score: int | None) -> int:
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        print(f"       [Esthétisme] URL: {url}")

        annee = self._get_annee_domaine(url)
        ps_pts = self._pagespeed_to_points(pagespeed_score)
        age_pts = self._annee_to_points(annee)

        print(f"       [Esthétisme] PageSpeed brut={pagespeed_score} → {ps_pts}pts")
        print(f"       [Esthétisme] Domaine création={annee} → {age_pts}pts")

        try:
            resp = httpx.get(
                url,
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
            )
            html = resp.text
            print(f"       [Esthétisme] HTTP {resp.status_code} ({len(html)} chars)")
        except Exception as e:
            print(f"       [Esthétisme] HTTP erreur: {e}")
            html = ""

        resp_pts, resp_detail = self._check_responsive(html)
        fw_pts, fw_detail = self._detect_framework(html)

        print(f"       [Esthétisme] Responsive → {resp_pts}pts ({resp_detail})")
        print(f"       [Esthétisme] Framework   → {fw_pts}pts ({fw_detail})")

        score = round(ps_pts * 0.40 + age_pts * 0.20 + resp_pts * 0.20 + fw_pts * 0.20)
        print(f"       [Esthétisme] Score final = {ps_pts}×0.4 + {age_pts}×0.2 + {resp_pts}×0.2 + {fw_pts}×0.2 = {score}/100")

        return score

    @staticmethod
    def _pagespeed_to_points(score: int | None) -> int:
        if score is None:
            return 50
        if score <= 30:
            return 0
        if score <= 50:
            return 30
        if score <= 70:
            return 60
        return 100

    @staticmethod
    def _get_annee_domaine(url: str) -> int | None:
        try:
            domain = whois_mod.whois(url)
            creation = domain.creation_date
            if isinstance(creation, list):
                creation = creation[0]
            return creation.year if creation else None
        except Exception:
            return None

    @staticmethod
    def _annee_to_points(annee: int | None) -> int:
        if annee is None:
            return 50
        age = datetime.now().year - annee
        if age > 8:
            return 0
        if age > 5:
            return 30
        if age > 2:
            return 60
        return 100

    @staticmethod
    def _check_responsive(html: str) -> tuple[int, str]:
        if not html:
            return 50, "erreur HTTP (neutre)"
        try:
            soup = BeautifulSoup(html, "html.parser")
            score = 0
            signals: list[str] = []

            if soup.find("meta", attrs={"name": "viewport"}):
                score += 40
                signals.append("viewport+40")

            styles = " ".join(tag.string or "" for tag in soup.find_all("style"))
            if "@media" in styles:
                score += 30
                signals.append("@media+30")

            full_html = html.lower()
            if any(u in full_html for u in ["vw", "vh", "rem", " em", "%"]):
                score += 20
                signals.append("unités-rel+20")

            if "width:9" not in full_html and "width:1" not in full_html:
                score += 10
                signals.append("pas-fixe+10")

            return score, " ".join(signals) or "aucun signal"
        except Exception as e:
            return 50, f"erreur ({e})"

    @staticmethod
    def _detect_framework(html: str) -> tuple[int, str]:
        if not html:
            return 50, "erreur HTTP (neutre)"
        try:
            soup = BeautifulSoup(html, "html.parser")
            sources = " ".join(
                tag.get("src", "") + tag.get("href", "")
                for tag in soup.find_all(["link", "script"])
            ) + html
            sources = sources.lower()

            modernes = ["tailwind", "bootstrap@5", "bootstrap/5", "bulma", "chakra"]
            semi = ["bootstrap@4", "bootstrap/4", "bootstrap@3", "bootstrap/3", "foundation", "materialize"]

            for kw in modernes:
                if kw in sources:
                    return 100, f"moderne ({kw})"
            for kw in semi:
                if kw in sources:
                    return 50, f"semi-moderne ({kw})"
            return 0, "aucun framework détecté"
        except Exception as e:
            return 50, f"erreur ({e})"
