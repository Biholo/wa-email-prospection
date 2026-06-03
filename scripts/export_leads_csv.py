#!/usr/bin/env python3
"""
export_leads_csv.py  (v2)
Génère 2 CSV Supabase → prêts à importer dans Brevo.
  data/export_email.csv     — liste 20  (email valide)
  data/export_whatsapp.csv  — liste 22  (mobile, tous)

Dépendances : pip install python-dotenv requests
"""

import csv
import os
import re
import time
import unicodedata
import logging
from datetime import date
from dotenv import load_dotenv
import requests

from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Configuration ──────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")

LOG_PATH  = "data/export.log"
PAGE_SIZE = 1_000

CSV_EMAIL    = "data/export_email.csv"
CSV_WHATSAPP = "data/export_whatsapp.csv"

LIST_EMAIL    = 20
LIST_WHATSAPP = 22

CSV_FIELDS = [
    "supabase_id", "EMAIL", "SMS", "TEL",
    "PRENOM", "NOM", "COMPANY",
    "ADDRESS", "WEBSITE_URL", "FACEBOOK_URL", "INSTAGRAM_URL",
    "AVERAGE_RATE", "NUMBER_OF_RATE", "CITY_ID",
    "JOB_TITLE", "CATEGORIE", "PROPRIETAIRE", "CANAL_PRINCIPAL",
    "COMMENTAIRE",
]

# ── Logging ────────────────────────────────────────────────────────────────
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# NORMALISATION & MAPPINGS
# ═══════════════════════════════════════════════════════════════════════════

def _normalize(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


_NICHE_MAP: dict[str, dict] = {
    "electricien":          {"label": "Électricien"},
    "serrurier":            {"label": "Serrurier"},
    "garage_auto":          {"label": "Garage automobile"},
    "clinique_dentaire":    {"label": "Clinique dentaire"},
    "architecte_interieur": {"label": "Architecte d'intérieur"},
    "architecte":           {"label": "Architecte"},
    "hotel":                {"label": "Hôtel 5 étoiles"},
    "cgp":                  {"label": "CGP"},
    "comptable":            {"label": "Expert-comptable"},
    "restaurant":           {"label": "Restaurant"},
    "iad":                  {"label": "IAD"},
    "agent_immo":           {"label": "Agent immobilier"},
}

_PROPRIETAIRE_MAP: dict[str, str] = {
    "Agent immobilier": "Immo Trust",
    "IAD":              "Immo Trust",
    "CGP":              "Rentium",
}


def map_categorie(niche_raw: str) -> str:
    entry = _NICHE_MAP.get(_normalize(niche_raw))
    return entry["label"] if entry else "Non catégorisé"


def map_proprietaire(categorie: str) -> str:
    return _PROPRIETAIRE_MAP.get(categorie, "Develly")


# ═══════════════════════════════════════════════════════════════════════════
# TÉLÉPHONE
# ═══════════════════════════════════════════════════════════════════════════

_STRIP_RE = re.compile(r"[\s\-\.\(\)/]")


def clean_phone(raw: str) -> str:
    if not raw:
        return ""
    p = _STRIP_RE.sub("", raw.strip())
    if p.startswith("+33"):
        p = "0" + p[3:]
    elif re.match(r"^33\d{9}$", p):
        p = "0" + p[2:]
    return p


def phone_type(raw: str) -> str:
    p = clean_phone(raw)
    if re.match(r"^0[67]\d{8}$", p):
        return "mobile"
    if re.match(r"^0[12345]\d{8}$", p) or re.match(r"^09\d{8}$", p):
        return "landline"
    return "invalid"


def to_e164(p: str) -> str:
    if not p:
        return ""
    if p.startswith("0"):
        return "+33" + p[1:]
    return p


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL
# ═══════════════════════════════════════════════════════════════════════════

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def is_valid_email(email: str) -> bool:
    return bool(email and "@" in email and _EMAIL_RE.match(email.strip()))


# ═══════════════════════════════════════════════════════════════════════════
# CANAL
# ═══════════════════════════════════════════════════════════════════════════

def determine_canal(email: str, phone: str, has_whatsapp) -> tuple[str, int]:
    """
    Email valide                           → EMAIL       (20)
    Mobile (tous)  → WHATSAPP (22)
    Sinon          → CALL     (25)
    """
    if is_valid_email(email):
        return "EMAIL", LIST_EMAIL
    if phone_type(phone) == "mobile":
        return "WHATSAPP", LIST_WHATSAPP
    return "CALL", 25


# ═══════════════════════════════════════════════════════════════════════════
# SKIP
# ═══════════════════════════════════════════════════════════════════════════

def should_skip(lead: dict) -> tuple[bool, str]:
    if lead.get("is_archived"):
        return True, "SKIP_ARCHIVED"
    if (lead.get("status") or "").upper() != "NEW":
        return True, "SKIP_STATUS"
    company = (lead.get("company") or "").strip()
    email   = (lead.get("email")   or "").strip()
    phone   = (lead.get("phone")   or "").strip()
    if not company and not email and not phone:
        return True, "SKIP_VIDE"
    return False, ""



# ═══════════════════════════════════════════════════════════════════════════
# SUPABASE
# ═══════════════════════════════════════════════════════════════════════════

_SB_COLS = ",".join([
    "id", "company", "niche", "google_category", "city_id", "address",
    "phone", "email", "contact_name", "website_url", "facebook_url",
    "instagram_url", "average_rate", "number_of_rate", "is_archived",
    "status", "is_mobile_phone", "has_whatsapp",
])


def fetch_sb_page(offset: int) -> list[dict]:
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/leads",
        headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Prefer":        "count=none",
        },
        params={
            "select": _SB_COLS,
            "order":  "id.asc",
            "limit":  PAGE_SIZE,
            "offset": offset,
            "or":     "(email.not.is.null,phone.ilike.06*,phone.ilike.07*,phone.ilike.+336*,phone.ilike.+337*)",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════════
# BUILD ROW
# ═══════════════════════════════════════════════════════════════════════════

def build_row(lead: dict, categorie: str, proprietaire: str, canal: str) -> dict:
    email_raw   = (lead.get("email")        or "").strip()
    phone_raw   = (lead.get("phone")        or "").strip()
    company     = (lead.get("company")      or "").strip()
    contact_nm  = (lead.get("contact_name") or "").strip()
    phone_clean = clean_phone(phone_raw)
    ptype       = phone_type(phone_raw)

    parts  = contact_nm.split(None, 1)
    prenom = parts[0] if parts else ""
    nom    = parts[1] if len(parts) > 1 else company

    return {
        "supabase_id":    lead.get("id"),
        "EMAIL":          email_raw if is_valid_email(email_raw) else "",
        "SMS":            to_e164(phone_clean) if ptype == "mobile"   else "",
        "TEL":            to_e164(phone_clean) if ptype == "landline" else "",
        "PRENOM":         prenom,
        "NOM":            nom,
        "COMPANY":        company,
        "ADDRESS":        lead.get("address")       or "",
        "WEBSITE_URL":    lead.get("website_url")   or "",
        "FACEBOOK_URL":   lead.get("facebook_url")  or "",
        "INSTAGRAM_URL":  lead.get("instagram_url") or "",
        "AVERAGE_RATE":   lead.get("average_rate")  or "",
        "NUMBER_OF_RATE": lead.get("number_of_rate") or "",
        "CITY_ID":        lead.get("city_id")       or "",
        "JOB_TITLE":      lead.get("google_category") or "",
        "CATEGORIE":      categorie,
        "PROPRIETAIRE":   proprietaire,
        "CANAL_PRINCIPAL": canal,
        "COMMENTAIRE":    f"Exporté depuis Supabase le {date.today().isoformat()}",
    }


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ SUPABASE_URL / SUPABASE_KEY manquants dans .env")
        raise SystemExit(1)

    # Sets en mémoire pour déduplique intra-run
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()

    stats = {
        "total": 0, "skip": 0, "dup": 0,
        "email": 0, "whatsapp": 0, "call_skip": 0,
    }

    t0     = time.time()
    offset = 0

    with (
        open(CSV_EMAIL,    "w", newline="", encoding="utf-8") as fe,
        open(CSV_WHATSAPP, "w", newline="", encoding="utf-8") as fw,
    ):
        writers = {
            LIST_EMAIL:    csv.DictWriter(fe, fieldnames=CSV_FIELDS),
            LIST_WHATSAPP: csv.DictWriter(fw, fieldnames=CSV_FIELDS),
        }
        for w in writers.values():
            w.writeheader()

        while True:
            log.info(f"━━━ Supabase — offset={offset} ━━━")
            try:
                page = fetch_sb_page(offset)
            except Exception as exc:
                log.error(f"Erreur Supabase offset={offset}: {exc}")
                break

            if not page:
                break

            for lead in page:
                sid = lead.get("id")
                stats["total"] += 1

                skip, reason = should_skip(lead)
                if skip:
                    stats["skip"] += 1
                    log.info(f"[SKIP] {sid} — {lead.get('company') or 'N/A'} ({reason})")
                    continue

                email_raw   = (lead.get("email") or "").strip()
                phone_raw   = (lead.get("phone") or "").strip()
                phone_clean = clean_phone(phone_raw)

                # Déduplique intra-run
                if is_valid_email(email_raw) and email_raw.lower() in seen_emails:
                    stats["dup"] += 1
                    continue
                if phone_clean and phone_clean in seen_phones:
                    stats["dup"] += 1
                    continue

                # Fixe sans email → skip
                if not is_valid_email(email_raw) and phone_type(phone_raw) == "landline":
                    stats["call_skip"] += 1
                    log.info(f"[SKIP] {sid} — {to_e164(phone_clean)} | {lead.get('company') or 'N/A'} (FIXE_SANS_EMAIL)")
                    continue

                # Aucun identifiant utilisable
                if not is_valid_email(email_raw) and not phone_clean:
                    stats["skip"] += 1
                    continue

                canal, liste_id = determine_canal(email_raw, phone_raw, None)

                # CALL = fixe avec email → traité via EMAIL ci-dessus, sinon skip
                if liste_id == 25:
                    stats["call_skip"] += 1
                    continue

                categorie    = map_categorie(lead.get("niche") or "")
                proprietaire = map_proprietaire(categorie)
                row          = build_row(lead, categorie, proprietaire, canal)

                writers[liste_id].writerow(row)

                if is_valid_email(email_raw):
                    seen_emails.add(email_raw.lower())
                if phone_clean:
                    seen_phones.add(phone_clean)

                if liste_id == LIST_EMAIL:
                    stats["email"] += 1
                else:
                    stats["whatsapp"] += 1

            offset += PAGE_SIZE
            if len(page) < PAGE_SIZE:
                break

    elapsed = time.time() - t0
    print(f"""
══════════════════════════════════
   EXPORT SUPABASE → CSV
══════════════════════════════════
Total leads traités    : {stats['total']}
─────────────────────────────────
✅ Email      (id=20)  : {stats['email']}    → {CSV_EMAIL}
✅ WhatsApp   (id=22)  : {stats['whatsapp']} → {CSV_WHATSAPP}
─────────────────────────────────
⏭️  Skippés            : {stats['skip']}
⏭️  Doublons           : {stats['dup']}
⏭️  Fixe sans email    : {stats['call_skip']}
─────────────────────────────────
⏱️  Durée              : {elapsed / 60:.1f} min
══════════════════════════════════""")


if __name__ == "__main__":
    main()
