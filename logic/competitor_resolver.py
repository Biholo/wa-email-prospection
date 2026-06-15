from __future__ import annotations

import math
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.supabase_service import SupabaseService

# Brevo CATEGORIE label → Supabase niche key
_LABEL_TO_KEY: dict[str, str] = {
    "electricien":            "electricien",
    "serrurier":              "serrurier",
    "garage automobile":      "garage_auto",
    "clinique dentaire":      "clinique_dentaire",
    "architecte d'interieur": "architecte_interieur",
    "architecte d interieur": "architecte_interieur",
    "architecte":             "architecte",
    "hotel 5 etoiles":        "hotel",
    "hotel":                  "hotel",
    "restaurant":             "restaurant",
    "plombier":               "plombier",
}


_UNCATEGORIZED: frozenset[str] = frozenset({"non_categorise", "non categorise"})


def _normalize_niche(label: str) -> str:
    """Convert Brevo CATEGORIE label to Supabase niche key."""
    s = label.lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return _LABEL_TO_KEY.get(s, s)


class CompetitorResolver:
    def __init__(self, supabase: "SupabaseService"):
        self._client = supabase._client

    def get_lead_data(self, email: str) -> dict:
        """Returns photos_count and is_google_verified for a lead email."""
        result = (
            self._client.table("leads")
            .select("photos_count, is_google_verified")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else {}

    def get_concurrents(
        self,
        angle: str,
        city_id: str,
        niche: str,
        lead_email: str = "",
        lead_note: float = 0.0,
        lead_avis: int = 0,
    ) -> dict:
        empty = {"nom": "", "note": 0.0, "nb_avis": 0, "site": "", "photos_count": None, "has_website": False, "is_google_verified": False}
        niche_raw = niche
        niche = _normalize_niche(niche)
        print(f"  [Resolver] niche brevo={niche_raw!r} → normalized={niche!r} | city_id={city_id!r}")

        # Enrich from Supabase if Brevo is missing city_id or has a generic niche
        if lead_email and (not city_id or not niche or niche in _UNCATEGORIZED):
            lead_row = self._lookup_lead(lead_email)
            if lead_row:
                if not city_id:
                    city_id = lead_row.get("city_id", "")
                if not niche or niche in _UNCATEGORIZED:
                    niche = lead_row.get("niche", "") or niche
                if not lead_note:
                    lead_note = float(lead_row.get("average_rate") or 0)
                if not lead_avis:
                    lead_avis = int(lead_row.get("number_of_rate") or 0)
                print(f"  [Resolver] Supabase enrichment → niche={niche!r} city_id={city_id!r}")
        # City name from cities table
        ville_nom = ""
        if city_id:
            city_result = (
                self._client.table("cities")
                .select("name")
                .eq("id", city_id)
                .limit(1)
                .execute()
            )
            ville_nom = city_result.data[0]["name"] if city_result.data else city_id

        if not city_id or not niche:
            return {
                "mode": "NORMAL",
                "ville_nom": ville_nom,
                "niche": niche,
                "lead_note": lead_note,
                "lead_avis": lead_avis,
                "concurrent_1": empty.copy(),
                "concurrent_2": empty.copy(),
                "ecart_note": 0.0,
                "ecart_avis": 0,
            }

        if angle == "INVISIBILITÉ":
            rows = self._query(city_id, niche, lead_email, with_site=True, order_by="number_of_rate")
            mode = "NORMAL"
            if not rows:
                mode = "NO_CONCURRENT_SITE"
                rows = self._query(city_id, niche, lead_email, with_site=False, order_by="number_of_rate")

        elif angle == "RÉPUTATION":
            rows = self._query_reputation(city_id, niche, lead_email, lead_note, min_avis=15)
            if len(rows) < 2:
                rows = self._query_reputation(city_id, niche, lead_email, lead_note, min_avis=0)
            rows = self._pick_top_reputation(rows)
            mode = "NORMAL"

        else:  # ESTHÉTISME, fallback
            rows = self._query(city_id, niche, lead_email, with_site=True, order_by="average_rate")
            mode = "NORMAL"

        names = [r.get("company", "?") for r in rows]
        print(f"  [Resolver] {angle} | {len(rows)} concurrent(s) : {names or '—'}")

        def to_c(row: dict) -> dict:
            return {
                "nom": row.get("company") or "",
                "note": float(row.get("average_rate") or 0),
                "nb_avis": int(row.get("number_of_rate") or 0),
                "site": row.get("website_url") or "",
                "photos_count": row.get("photos_count"),
                "has_website": bool(row.get("has_website") or row.get("website_url")),
                "is_google_verified": bool(row.get("is_google_verified")),
            }

        c1 = to_c(rows[0]) if len(rows) > 0 else empty.copy()
        c2 = to_c(rows[1]) if len(rows) > 1 else empty.copy()
        c3 = to_c(rows[2]) if len(rows) > 2 else empty.copy()

        best_note = max(c1["note"], c2["note"])
        best_avis = max(c1["nb_avis"], c2["nb_avis"])

        return {
            "mode": mode,
            "ville_nom": ville_nom,
            "niche": niche,
            "lead_note": lead_note,
            "lead_avis": lead_avis,
            "concurrent_1": c1,
            "concurrent_2": c2,
            "concurrent_3": c3,
            "ecart_note": round(best_note - lead_note, 1),
            "ecart_avis": best_avis - lead_avis,
        }

    def get_reputation_candidates(
        self,
        city_id: str,
        niche: str,
        lead_email: str,
        lead_note: float,
    ) -> list[dict]:
        niche = _normalize_niche(niche)
        if not city_id or not niche:
            return []
        rows = self._query_reputation(city_id, niche, lead_email, lead_note, min_avis=0)
        return [
            {
                "nom": r.get("company") or "",
                "note": float(r.get("average_rate") or 0),
                "nb_avis": int(r.get("number_of_rate") or 0),
            }
            for r in rows
        ]

    def archive_lead(self, email: str) -> None:
        self._client.table("leads").update({"is_archived": True}).eq("email", email).execute()

    def _lookup_lead(self, email: str) -> dict:
        result = (
            self._client.table("leads")
            .select("city_id, niche, average_rate, number_of_rate, photos_count, is_google_verified")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else {}

    def _query(
        self,
        city_id: str,
        niche: str,
        lead_email: str,
        with_site: bool,
        order_by: str,
    ) -> list[dict]:
        q = (
            self._client.table("leads")
            .select("company, average_rate, number_of_rate, website_url, photos_count, has_website, is_google_verified")
            .eq("city_id", city_id)
            .eq("niche", niche)
            .eq("is_archived", False)
            .order(order_by, desc=True)
            .limit(3)
        )
        if lead_email:
            q = q.neq("email", lead_email)
        if with_site:
            q = q.not_.is_("website_url", "null")
        q = q.lt("number_of_rate", 1500)
        return q.execute().data or []

    def _query_reputation(
        self,
        city_id: str,
        niche: str,
        lead_email: str,
        lead_note: float,
        min_avis: int,
    ) -> list[dict]:
        q = (
            self._client.table("leads")
            .select("company, average_rate, number_of_rate, website_url, photos_count, has_website, is_google_verified")
            .eq("city_id", city_id)
            .eq("niche", niche)
            .eq("is_archived", False)
            .gt("average_rate", lead_note)
            .order("average_rate", desc=True)
            .limit(10)
        )
        if lead_email:
            q = q.neq("email", lead_email)
        if min_avis > 0:
            q = q.gte("number_of_rate", min_avis)
        q = q.lt("number_of_rate", 1500)
        return q.execute().data or []

    @staticmethod
    def _score_reputation(row: dict) -> float:
        note = float(row.get("average_rate") or 0)
        nb_avis = int(row.get("number_of_rate") or 0)
        if note == 0:
            return 0.0
        if nb_avis == 0:
            return note
        return note * math.log(nb_avis + 1)

    @staticmethod
    def _pick_top_reputation(rows: list[dict]) -> list[dict]:
        return sorted(rows, key=CompetitorResolver._score_reputation, reverse=True)[:3]
