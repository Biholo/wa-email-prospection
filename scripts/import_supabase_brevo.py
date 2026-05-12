#!/usr/bin/env python3
"""
import_supabase_brevo.py
Import leads Supabase → Brevo (listes 21=email, 22=whatsapp, 25=call)

Dépendances : pip install python-dotenv requests
"""

import os
import re
import time
import sqlite3
import unicodedata
import logging
from datetime import datetime, date, timezone
from dotenv import load_dotenv
import requests

from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Configuration ──────────────────────────────────────────────────────────
SUPABASE_URL  = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY  = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")

DB_PATH          = "data/import.db"
LOG_PATH         = "data/import.log"
PAGE_SIZE        = 1_000   # leads par page Supabase
BATCH_SIZE       = 150     # imports avant sleep
SLEEP_AFTER_BATCH = 0.2   # secondes

BREVO_BASE    = "https://api.brevo.com/v3"
LIST_EMAIL    = 20   # ATTENTE ENVOI EMAIL
LIST_WHATSAPP = 22
LIST_CALL     = 25

# ── Initialisation dossier data + logging ─────────────────────────────────
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
    """Lowercase + suppression accents."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


# Niche DB (normalisée, sans accents) → Label unifié Brevo
_NICHE_MAP: dict[str, str] = {
    "agent immobilier":                     "Agent immobilier",
    "iad":                                  "IAD",
    "conseiller en gestion de patrimoine":  "CGP",
    "architecte":                           "Architecte",
    "architecte d'interieur":               "Architecte d'intérieur",
    "asiatique a volonte":                  "Restaurant",
    "restaurant a volonte":                 "Restaurant",
    "restaurant asiatique":                 "Restaurant",
    "cliniques dentaires":                  "Clinique dentaire",
    "comptable":                            "Expert-comptable",
    "expert comptable":                     "Expert-comptable",
    "experts-comptables":                   "Expert-comptable",
    "electricien":                          "Electricien",
    "garage auto":                          "Garage auto",
    "hotel 5 etoiles":                      "Hôtel",
    "plombier":                             "Plombier",
    "serrurier":                            "Serrurier",
}

# Label unifié → PROPRIETAIRE
_PROPRIETAIRE_MAP: dict[str, str] = {
    "Agent immobilier":      "Immo Trust",
    "IAD":                   "Immo Trust",
    "CGP":                   "Rentium",
}


def map_categorie(niche_raw: str) -> str:
    return _NICHE_MAP.get(_normalize(niche_raw), "Non catégorisé")


def map_proprietaire(categorie: str) -> str:
    return _PROPRIETAIRE_MAP.get(categorie, "Develly")


# ═══════════════════════════════════════════════════════════════════════════
# TÉLÉPHONE
# ═══════════════════════════════════════════════════════════════════════════

_STRIP_RE = re.compile(r"[\s\-\.\(\)/]")


def clean_phone(raw: str) -> str:
    """Normalise vers 0XXXXXXXXX. Retourne '' si vide."""
    if not raw:
        return ""
    p = _STRIP_RE.sub("", raw.strip())
    if p.startswith("+33"):
        p = "0" + p[3:]
    elif re.match(r"^33\d{9}$", p):
        p = "0" + p[2:]
    return p


def phone_type(raw: str) -> str:
    """'mobile' | 'landline' | 'invalid'"""
    p = clean_phone(raw)
    if re.match(r"^0[67]\d{8}$", p):
        return "mobile"
    if re.match(r"^0[12345]\d{8}$", p) or re.match(r"^09\d{8}$", p):
        return "landline"
    return "invalid"


def to_e164(p: str) -> str:
    """Convertit 0XXXXXXXXX → +33XXXXXXXXX pour Brevo."""
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
# CANAL / LISTE
# ═══════════════════════════════════════════════════════════════════════════

def determine_canal(email: str, phone: str) -> tuple[str, int]:
    """
    Email valide                     → EMAIL  (21)
    Pas email valide + mobile 06/07  → WHATSAPP (22)
    Sinon                            → CALL   (25)
    """
    if is_valid_email(email):
        return "EMAIL", LIST_EMAIL
    if phone_type(phone) == "mobile":
        return "WHATSAPP", LIST_WHATSAPP
    return "CALL", LIST_CALL


# ═══════════════════════════════════════════════════════════════════════════
# RÈGLES DE SKIP
# ═══════════════════════════════════════════════════════════════════════════

def _is_future_date(dnc) -> bool:
    if not dnc:
        return False
    try:
        s = str(dnc)
        if "T" in s or "Z" in s:
            s = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
            return dt > now
        return date.fromisoformat(s[:10]) > date.today()
    except (ValueError, TypeError):
        return False


def should_skip(lead: dict) -> tuple[bool, str]:
    """Retourne (skip, raison)."""
    if lead.get("is_archived"):
        return True, "SKIP_ARCHIVED"
    if (lead.get("status") or "").upper() != "NEW":
        return True, "SKIP_STATUS"
    if lead.get("anti_bot"):
        return True, "SKIP_ANTIBOT"
    if _is_future_date(lead.get("do_not_call_before")):
        return True, "SKIP_DNC"
    company = (lead.get("company") or "").strip()
    email   = (lead.get("email")   or "").strip()
    phone   = (lead.get("phone")   or "").strip()
    if not company and not email and not phone:
        return True, "SKIP_VIDE"
    return False, ""


# ═══════════════════════════════════════════════════════════════════════════
# SQLITE
# ═══════════════════════════════════════════════════════════════════════════

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS import_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            supabase_id     BIGINT,
            email           TEXT,
            phone           TEXT,
            company         TEXT,
            niche_raw       TEXT,
            categorie       TEXT,
            proprietaire    TEXT,
            canal           TEXT,
            liste_id        INTEGER,
            status          TEXT,
            raison          TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sid    ON import_logs(supabase_id);
        CREATE INDEX IF NOT EXISTS idx_status ON import_logs(status);
    """)
    conn.commit()


def already_done(conn: sqlite3.Connection, sid: int) -> bool:
    """True si déjà traité avec succès ou skip (pas en erreur → on retente)."""
    row = conn.execute(
        "SELECT 1 FROM import_logs WHERE supabase_id=? AND status != 'ERREUR' LIMIT 1",
        (sid,),
    ).fetchone()
    return row is not None


def log_row(conn: sqlite3.Connection, **kw) -> None:
    conn.execute(
        """INSERT INTO import_logs
           (supabase_id, email, phone, company, niche_raw, categorie,
            proprietaire, canal, liste_id, status, raison)
           VALUES
           (:supabase_id, :email, :phone, :company, :niche_raw, :categorie,
            :proprietaire, :canal, :liste_id, :status, :raison)""",
        kw,
    )
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
# SUPABASE
# ═══════════════════════════════════════════════════════════════════════════

_SB_COLS = ",".join([
    "id", "company", "niche", "city_id", "address", "phone", "email",
    "contact_name", "website_url", "facebook_url", "instagram_url",
    "average_rate", "number_of_rate", "is_archived", "status",
    "do_not_call_before", "anti_bot",
])


def fetch_sb_page(offset: int) -> list[dict]:
    url     = f"{SUPABASE_URL}/rest/v1/leads"
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer":        "count=none",
    }
    params = {
        "select": _SB_COLS,
        "order":  "id.asc",
        "limit":  PAGE_SIZE,
        "offset": offset,
    }
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════════
# BREVO
# ═══════════════════════════════════════════════════════════════════════════

def _bh() -> dict:
    return {
        "accept":        "application/json",
        "content-type":  "application/json",
        "api-key":       BREVO_API_KEY,
    }


def preload_brevo(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """
    Charge en mémoire tous les emails + téléphones déjà présents dans Brevo
    et dans le SQLite local (session précédente). Évite 1 appel API par lead.
    """
    emails: set[str] = set()
    phones: set[str] = set()

    # Depuis SQLite (imports précédents)
    for row in conn.execute(
        "SELECT email, phone FROM import_logs WHERE status='IMPORTÉ'"
    ):
        if row[0]:
            emails.add(row[0].lower().strip())
        if row[1]:
            phones.add(clean_phone(row[1]))

    # Depuis Brevo
    log.info("━━━ Préchargement Brevo ━━━")
    offset, limit = 0, 1_000
    while True:
        try:
            resp = requests.get(
                f"{BREVO_BASE}/contacts",
                headers=_bh(),
                params={"limit": limit, "offset": offset, "sort": "asc"},
                timeout=30,
            )
            if resp.status_code != 200:
                log.warning(f"GET /contacts HTTP {resp.status_code} — arrêt préchargement")
                break
            data     = resp.json()
            contacts = data.get("contacts", [])
            if not contacts:
                break
            for c in contacts:
                if c.get("email"):
                    emails.add(c["email"].lower().strip())
                attrs = c.get("attributes") or {}
                for attr_key in ("SMS", "TEL"):
                    v = attrs.get(attr_key)
                    if v:
                        phones.add(clean_phone(str(v)))
            offset += limit
            log.info(f"  {offset} contacts Brevo chargés...")
            if len(contacts) < limit:
                break
        except Exception as exc:
            log.error(f"Erreur préchargement Brevo: {exc}")
            break

    log.info(
        f"✅ Préchargement OK — {len(emails)} emails / {len(phones)} téléphones en mémoire"
    )
    return emails, phones


def build_payload(
    lead: dict,
    categorie: str,
    proprietaire: str,
    canal: str,
    liste_id: int,
) -> dict:
    email_raw  = (lead.get("email")        or "").strip()
    phone_raw  = (lead.get("phone")        or "").strip()
    company    = (lead.get("company")      or "").strip()
    contact_nm = (lead.get("contact_name") or "").strip()
    phone_clean = clean_phone(phone_raw)
    ptype       = phone_type(phone_raw)

    parts  = contact_nm.split(None, 1)
    prenom = parts[0] if parts else ""
    nom    = parts[1] if len(parts) > 1 else company

    attrs: dict = {
        "PRENOM":            prenom  or None,
        "NOM":               nom     or None,
        "COMPANY":           company or None,
        "ADDRESS":           (lead.get("address")       or "") or None,
        "WEBSITE_URL":       (lead.get("website_url")   or "") or None,
        "FACEBOOK_URL":      (lead.get("facebook_url")  or "") or None,
        "INSTAGRAM_URL":     (lead.get("instagram_url") or "") or None,
        "AVERAGE_RATE":      lead.get("average_rate"),
        "NUMBER_OF_RATE":    lead.get("number_of_rate"),
        "CITY_ID":           lead.get("city_id"),
        "JOB_TITLE":         categorie,
        "CATEGORIE":         categorie,
        "PROPRIETAIRE":      proprietaire,
        "CANAL_PRINCIPAL":   canal,
        "NOMBRE_DE_CONTACT": 0,
        "COMMENTAIRE":       f"Importé depuis Supabase le {date.today().isoformat()}",
    }
    # Filtre None + chaînes vides ; garde les 0 numériques
    attrs = {
        k: v for k, v in attrs.items()
        if v is not None and v != ""
    }

    if ptype == "mobile":
        attrs["SMS"] = to_e164(phone_clean)
    elif ptype == "landline":
        attrs["TEL"] = to_e164(phone_clean)

    payload: dict = {
        "attributes":    attrs,
        "listIds":       [liste_id],
        "updateEnabled": False,
    }
    if is_valid_email(email_raw):
        payload["email"] = email_raw

    return payload


def brevo_create(payload: dict) -> tuple[bool, str]:
    """Crée un contact Brevo. Retourne (succès, message_erreur)."""
    try:
        resp = requests.post(
            f"{BREVO_BASE}/contacts",
            headers=_bh(),
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return True, ""
        data = resp.json()
        return False, data.get("message", str(resp.status_code))
    except Exception as exc:
        return False, str(exc)


# ═══════════════════════════════════════════════════════════════════════════
# RAPPORT
# ═══════════════════════════════════════════════════════════════════════════

def print_report(stats: dict, elapsed_s: float) -> None:
    m = elapsed_s / 60
    print(f"""
══════════════════════════════════
   RAPPORT IMPORT SUPABASE → BREVO
══════════════════════════════════
Total leads traités    : {stats['total']}
─────────────────────────────────
✅ Importés email      : {stats['imp_email']} (id=20)
✅ Importés WhatsApp   : {stats['imp_wa']} (id=22)
📞 À appeler           : {stats['imp_call']} (id=25)
─────────────────────────────────
Par propriétaire :
  Develly              : {stats['prop']['Develly']}
  Immo Trust           : {stats['prop']['Immo Trust']}
  Rentium              : {stats['prop']['Rentium']}
─────────────────────────────────
⏭️  Skippés doublons   : {stats['skip_dup']}
⏭️  Skippés archivés   : {stats['skip_arch']}
⏭️  Skippés anti-bot   : {stats['skip_bot']}
⏭️  Skippés invalides  : {stats['skip_inv']}
❌ Erreurs API          : {stats['errors']}
─────────────────────────────────
⏱️  Durée totale       : {m:.1f} min
══════════════════════════════════""")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    if not SUPABASE_URL or not SUPABASE_KEY or not BREVO_API_KEY:
        print(
            "❌ Variables d'environnement manquantes.\n"
            "   Créez un fichier .env avec SUPABASE_URL, SUPABASE_KEY, BREVO_API_KEY"
        )
        raise SystemExit(1)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    existing_emails, existing_phones = preload_brevo(conn)

    stats: dict = {
        "total":     0,
        "imp_email": 0,
        "imp_wa":    0,
        "imp_call":  0,
        "skip_dup":  0,
        "skip_arch": 0,
        "skip_bot":  0,
        "skip_inv":  0,
        "errors":    0,
        "prop":      {"Develly": 0, "Immo Trust": 0, "Rentium": 0},
    }

    t0           = time.time()
    offset       = 0
    import_count = 0  # compteur pour le sleep par batch

    try:
        while True:
            log.info(f"━━━ Supabase — page offset={offset} ━━━")
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

                # ── Reprise : déjà traité ? ────────────────────────────
                if already_done(conn, sid):
                    continue

                # ── Skip rules ─────────────────────────────────────────
                skip, reason = should_skip(lead)
                if skip:
                    if reason == "SKIP_ARCHIVED":
                        stats["skip_arch"] += 1
                    elif reason == "SKIP_ANTIBOT":
                        stats["skip_bot"] += 1
                    else:
                        stats["skip_inv"] += 1
                    log.info(f"[SKIP] Lead {sid} — {lead.get('company') or 'N/A'} ({reason})")
                    log_row(
                        conn,
                        supabase_id=sid,
                        email=lead.get("email") or "",
                        phone=lead.get("phone") or "",
                        company=lead.get("company") or "",
                        niche_raw=lead.get("niche") or "",
                        categorie="", proprietaire="",
                        canal="", liste_id=None,
                        status="SKIPPÉ", raison=reason,
                    )
                    continue

                # ── Mapping ────────────────────────────────────────────
                categorie    = map_categorie(lead.get("niche") or "")
                proprietaire = map_proprietaire(categorie)
                email_raw    = (lead.get("email") or "").strip()
                phone_raw    = (lead.get("phone") or "").strip()
                phone_clean  = clean_phone(phone_raw)
                canal, liste_id = determine_canal(email_raw, phone_raw)

                # ── Doublons (sets en mémoire) ─────────────────────────
                if is_valid_email(email_raw) and email_raw.lower() in existing_emails:
                    stats["skip_dup"] += 1
                    log_row(
                        conn,
                        supabase_id=sid,
                        email=email_raw, phone=phone_clean,
                        company=lead.get("company") or "",
                        niche_raw=lead.get("niche") or "",
                        categorie=categorie, proprietaire=proprietaire,
                        canal=canal, liste_id=liste_id,
                        status="SKIPPÉ", raison="DOUBLON_EMAIL",
                    )
                    continue

                if phone_clean and phone_clean in existing_phones:
                    stats["skip_dup"] += 1
                    log_row(
                        conn,
                        supabase_id=sid,
                        email=email_raw, phone=phone_clean,
                        company=lead.get("company") or "",
                        niche_raw=lead.get("niche") or "",
                        categorie=categorie, proprietaire=proprietaire,
                        canal=canal, liste_id=liste_id,
                        status="SKIPPÉ", raison="DOUBLON_PHONE",
                    )
                    continue

                # ── Fixe sans email → skip (Brevo refuse TEL comme identifiant) ──
                if not is_valid_email(email_raw) and phone_type(phone_raw) == "landline":
                    stats["skip_inv"] += 1
                    log.info(f"[SKIP] Lead {sid} — {to_e164(phone_clean)} | {lead.get('company') or 'N/A'} (FIXE_SANS_EMAIL)")
                    log_row(
                        conn,
                        supabase_id=sid,
                        email=email_raw, phone=phone_clean,
                        company=lead.get("company") or "",
                        niche_raw=lead.get("niche") or "",
                        categorie=categorie, proprietaire=proprietaire,
                        canal="CALL", liste_id=LIST_CALL,
                        status="SKIPPÉ", raison="FIXE_SANS_EMAIL",
                    )
                    continue

                # ── Identifiant Brevo obligatoire ──────────────────────
                # Brevo requiert email OU SMS. TEL seul peut être refusé.
                if not is_valid_email(email_raw) and not phone_clean:
                    stats["skip_inv"] += 1
                    log_row(
                        conn,
                        supabase_id=sid,
                        email=email_raw, phone=phone_clean,
                        company=lead.get("company") or "",
                        niche_raw=lead.get("niche") or "",
                        categorie=categorie, proprietaire=proprietaire,
                        canal=canal, liste_id=liste_id,
                        status="SKIPPÉ", raison="INVALIDE",
                    )
                    continue

                # ── Construction payload + envoi ───────────────────────
                payload = build_payload(
                    lead, categorie, proprietaire, canal, liste_id
                )
                success, msg = brevo_create(payload)
                import_count += 1

                if success:
                    # Mise à jour sets pour éviter doublons intra-session
                    if is_valid_email(email_raw):
                        existing_emails.add(email_raw.lower())
                    if phone_clean:
                        existing_phones.add(phone_clean)

                    log_row(
                        conn,
                        supabase_id=sid,
                        email=email_raw, phone=phone_clean,
                        company=lead.get("company") or "",
                        niche_raw=lead.get("niche") or "",
                        categorie=categorie, proprietaire=proprietaire,
                        canal=canal, liste_id=liste_id,
                        status="IMPORTÉ", raison="",
                    )
                    stats["prop"][proprietaire] = (
                        stats["prop"].get(proprietaire, 0) + 1
                    )
                    if liste_id == LIST_EMAIL:
                        stats["imp_email"] += 1
                    elif liste_id == LIST_WHATSAPP:
                        stats["imp_wa"] += 1
                    else:
                        stats["imp_call"] += 1
                else:
                    log.error(
                        f"[ERREUR] Lead {sid} — {lead.get('company') or 'N/A'} "
                        f"| canal={canal} | téléphone={phone_clean} | email={email_raw} "
                        f"→ {msg}"
                    )
                    log_row(
                        conn,
                        supabase_id=sid,
                        email=email_raw, phone=phone_clean,
                        company=lead.get("company") or "",
                        niche_raw=lead.get("niche") or "",
                        categorie=categorie, proprietaire=proprietaire,
                        canal=canal, liste_id=liste_id,
                        status="ERREUR", raison="ERREUR_API",
                    )
                    stats["errors"] += 1

                # ── Rate limiting : sleep après chaque batch de 150 ────
                if import_count % BATCH_SIZE == 0:
                    log.info(
                        f"━━━ Batch {import_count // BATCH_SIZE} — "
                        f"{import_count} importés / "
                        f"{stats['skip_dup']} doublons / "
                        f"{stats['errors']} erreurs ━━━"
                    )
                    time.sleep(SLEEP_AFTER_BATCH)

            offset += PAGE_SIZE
            if len(page) < PAGE_SIZE:
                break  # dernière page

    finally:
        conn.close()

    print_report(stats, time.time() - t0)


if __name__ == "__main__":
    main()
