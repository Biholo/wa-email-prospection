import math
import re
from datetime import datetime

import httpx
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
                continue

            if angle == "INVISIBILITÉ":
                site = attrs.get("WEBSITE_URL") or ""
                if not site.strip():
                    return "INVISIBILITÉ"

            elif angle == "RÉPUTATION":
                note_raw = attrs.get("AVERAGE_RATE")
                if note_raw not in (None, ""):
                    try:
                        lead_note = float(note_raw)
                        triggered, _ = self._can_trigger_reputation(lead_note, candidates)
                        if triggered:
                            return "RÉPUTATION"
                    except (ValueError, TypeError):
                        pass

            elif angle == "ESTHÉTISME":
                site = attrs.get("WEBSITE_URL") or ""
                if site.strip():
                    score = self._score_esthetisme(site.strip(), pagespeed_score)
                    if score < 50:
                        return "ESTHÉTISME"

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

        ps_pts = self._pagespeed_to_points(pagespeed_score)

        try:
            resp = httpx.get(
                url,
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
            )
            html = resp.text
        except Exception:
            html = ""

        if html:
            red_flags = self._check_red_flags(html)
            if red_flags:
                return 0

        resp_pts, _ = self._check_responsive(html)
        fw_pts, _ = self._detect_framework(html)

        score = round(ps_pts * 0.50 + resp_pts * 0.25 + fw_pts * 0.25)
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
    def _check_red_flags(html: str) -> list[str]:
        flags = []
        try:
            soup = BeautifulSoup(html, "html.parser")
            now_year = datetime.now().year

            # 1. Copyright year in footer > 2 ans
            footer = soup.find("footer")
            copyright_area = footer.get_text(" ", strip=True) if footer else html[-3000:]
            copyright_blocks = re.findall(r'(?:©|copyright).{0,80}', copyright_area.lower())
            most_recent_year = None
            for block in copyright_blocks:
                years = [int(y) for y in re.findall(r'\d{4}', block) if 2000 <= int(y) <= now_year]
                if years:
                    most_recent_year = max(most_recent_year or 0, max(years))
            if most_recent_year and (now_year - most_recent_year) > 2:
                flags.append(f"copyright {most_recent_year} (+{now_year - most_recent_year} ans)")

            # 2. Pas de mentions légales
            full_lower = html.lower()
            legal_kw = [
                "mentions légales", "mentions-légales", "mentions_légales",
                "politique de confidentialité", "données personnelles",
                "cgu", "cgv",
            ]
            if not any(kw in full_lower for kw in legal_kw):
                flags.append("pas de mentions légales")

            # 3a. Plusieurs H1
            h1s = soup.find_all("h1")
            if len(h1s) > 1:
                flags.append(f"{len(h1s)} balises H1")

            # 3b. Pas de balise Title
            title = soup.find("title")
            if not title or not title.get_text(strip=True):
                flags.append("pas de balise Title")

        except Exception:
            pass

        return flags

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
