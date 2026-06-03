#!/usr/bin/env python3
"""
update_categorie_brevo.py
Load Brevo contacts page by page, lookup niche in Supabase by phone (SMS), update CATEGORIE by Brevo contact ID.

Usage:
    python scripts/update_categorie_brevo.py           # live run
    python scripts/update_categorie_brevo.py --dry-run
"""

import os
import re
import sys
import time
import logging
import argparse
import unicodedata
from collections import deque
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services.supabase_service import SupabaseService

BREVO_BASE = "https://api.brevo.com/v3"
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_PAGE_SIZE = 500
SUPA_PAGE_SIZE = 1_000
MAX_CALLS_PER_SEC = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

_STRIP_RE = re.compile(r"[\s\-\.\(\)/]")


def normalize_company(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def normalize_phone(raw: str) -> str:
    """Normalize any French phone to E.164 (+33XXXXXXXXX). Returns '' if invalid."""
    if not raw:
        return ""
    p = _STRIP_RE.sub("", raw.strip())
    if p.startswith("+33") and len(p) == 12:
        return p
    if p.startswith("0033") and len(p) == 13:
        return "+" + p[2:]
    if re.match(r"^33\d{9}$", p):
        return "+" + p
    if p.startswith("0") and len(p) == 10:
        return "+33" + p[1:]
    return ""


# ── Supabase: load maps once ───────────────────────────────────────────────────

def load_supabase_maps(supa: SupabaseService) -> tuple[dict[str, str], dict[str, str]]:
    """Returns ({e164_phone: niche}, {normalized_company: niche})."""
    phone_map: dict[str, str] = {}
    company_map: dict[str, str] = {}
    offset = 0
    while True:
        resp = (
            supa._client.table("leads")
            .select("phone, company, niche")
            .not_.is_("niche", "null")
            .neq("niche", "")
            .range(offset, offset + SUPA_PAGE_SIZE - 1)
            .execute()
        )
        batch = resp.data or []
        for row in batch:
            niche = row.get("niche") or ""
            e164 = normalize_phone(row.get("phone") or "")
            if e164:
                phone_map[e164] = niche
            company = normalize_company(row.get("company") or "")
            if company:
                company_map[company] = niche
        log.info("Supabase loaded %d rows (phone=%d company=%d)", len(batch), len(phone_map), len(company_map))
        if len(batch) < SUPA_PAGE_SIZE:
            break
        offset += SUPA_PAGE_SIZE
    return phone_map, company_map


# ── Brevo pagination ───────────────────────────────────────────────────────────

def fetch_brevo_page(client: httpx.Client, offset: int) -> tuple[list[dict], int]:
    resp = client.get(
        f"{BREVO_BASE}/contacts",
        params={"limit": BREVO_PAGE_SIZE, "offset": offset},
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("contacts", []), data.get("count", 0)


def update_by_id(client: httpx.Client, contact_id: int, categorie: str) -> None:
    resp = client.put(
        f"{BREVO_BASE}/contacts/{contact_id}",
        json={"attributes": {"CATEGORIE": categorie}},
    )
    resp.raise_for_status()


# ── Main ───────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, force: bool = False) -> None:
    supa = SupabaseService()

    log.info("Loading Supabase maps...")
    phone_map, company_map = load_supabase_maps(supa)
    log.info("Maps loaded: phone=%d company=%d", len(phone_map), len(company_map))

    stats = {"updated": 0, "errors": 0, "matched_phone": 0, "matched_company": 0,
             "skip_already_done": 0, "skip_no_sms": 0, "skip_invalid_phone": 0, "skip_not_in_supabase": 0}
    call_times: deque = deque()
    offset = 0
    total = None

    with httpx.Client(
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
        timeout=30,
    ) as client:
        while True:
            contacts, total = fetch_brevo_page(client, offset)
            if not contacts:
                break

            if offset == 0:
                log.info("Brevo total contacts: %d", total)

            for contact in contacts:
                contact_id = contact.get("id")
                attrs = contact.get("attributes") or {}

                modified_at = contact.get("modifiedAt", "")
                if not force and modified_at.startswith(date.today().isoformat()):
                    stats["skip_already_done"] += 1
                    continue

                sms_raw = attrs.get("SMS", "")
                phone = normalize_phone(sms_raw)
                niche = None
                match_by = None

                if not sms_raw:
                    stats["skip_no_sms"] += 1
                elif not phone:
                    stats["skip_invalid_phone"] += 1
                else:
                    niche = phone_map.get(phone)
                    if niche:
                        match_by = "phone"
                        stats["matched_phone"] += 1

                if not niche:
                    nom = normalize_company(attrs.get("NOM", ""))
                    if nom:
                        niche = company_map.get(nom)
                        if niche:
                            match_by = "company"
                            stats["matched_company"] += 1

                if not niche:
                    stats["skip_not_in_supabase"] += 1
                    continue

                if dry_run:
                    log.info("[DRY-RUN] id=%s (%s) -> CATEGORIE=%s [by %s]", contact_id, phone or attrs.get("NOM", ""), niche, match_by)
                    stats["updated"] += 1
                    continue

                now = time.monotonic()
                call_times.append(now)
                if len(call_times) > MAX_CALLS_PER_SEC:
                    call_times.popleft()
                if len(call_times) == MAX_CALLS_PER_SEC:
                    elapsed = now - call_times[0]
                    if elapsed < 1.0:
                        time.sleep(1.0 - elapsed)

                try:
                    update_by_id(client, contact_id, niche)
                    stats["updated"] += 1
                except Exception as exc:
                    log.warning("Failed id=%s sms=%s: %s", contact_id, phone, exc)
                    stats["errors"] += 1

            log.info(
                "Page offset=%d/%d done (updated=%d errors=%d | skips: already_done=%d no_sms=%d invalid_phone=%d not_in_supabase=%d)",
                offset, total, stats["updated"], stats["errors"],
                stats["skip_already_done"], stats["skip_no_sms"], stats["skip_invalid_phone"], stats["skip_not_in_supabase"],
            )

            offset += len(contacts)
            if offset >= total:
                break

    log.info(
        "Done. updated=%d errors=%d | matched: phone=%d company=%d | skips: already_done=%d no_sms=%d invalid_phone=%d not_in_supabase=%d",
        stats["updated"], stats["errors"],
        stats["matched_phone"], stats["matched_company"],
        stats["skip_already_done"], stats["skip_no_sms"], stats["skip_invalid_phone"], stats["skip_not_in_supabase"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync CATEGORIE Brevo <- Supabase niche (by phone)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to Brevo")
    parser.add_argument("--force", action="store_true", help="Re-process contacts already having CATEGORIE set")
    args = parser.parse_args()
    run(dry_run=args.dry_run, force=args.force)
