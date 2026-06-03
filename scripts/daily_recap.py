"""Daily scraping recap sent via WhatsApp to Kilian."""
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services.supabase_service import SupabaseService
from services.wasender_service import WasenderService

KILIAN_NUMERO = "+33641552699"


# ── Data fetching ─────────────────────────────────────────────────────────────


def _yesterday_range():
    yesterday = date.today() - timedelta(days=1)
    start = f"{yesterday.isoformat()}T00:00:00+00:00"
    end = f"{date.today().isoformat()}T00:00:00+00:00"
    return yesterday, start, end


def fetch_leads_by_niche(supa: SupabaseService) -> tuple[dict, date]:
    yesterday, start, end = _yesterday_range()
    resp = (
        supa._client.table("leads")
        .select("niche, email, has_whatsapp")
        .gte("created_at", start)
        .lt("created_at", end)
        .execute()
    )
    stats: dict = defaultdict(lambda: {"total": 0, "with_email": 0, "with_wa": 0})
    for row in resp.data or []:
        n = row.get("niche") or "unknown"
        stats[n]["total"] += 1
        if row.get("email"):
            stats[n]["with_email"] += 1
        if row.get("has_whatsapp"):
            stats[n]["with_wa"] += 1
    return dict(stats), yesterday


def fetch_scrape_progress(supa: SupabaseService) -> dict:
    _, start, end = _yesterday_range()
    resp = (
        supa._client.table("scrape_progress")
        .select("status")
        .gte("scraped_at", start)
        .lt("scraped_at", end)
        .execute()
    )
    counts = {"done": 0, "error": 0, "pending": 0}
    for row in resp.data or []:
        s = row.get("status")
        if s in counts:
            counts[s] += 1
    return counts


# ── Message builders ──────────────────────────────────────────────────────────


def build_kilian_message(stats: dict, scrape: dict, yesterday: date) -> str:
    lines = []
    total_leads = total_email = total_wa = 0
    for niche, s in sorted(stats.items(), key=lambda x: -x[1]["total"]):
        if s["total"] == 0:
            continue
        lines.append(f"• {s['total']} leads sur {niche}")
        total_leads += s["total"]
        total_email += s["with_email"]
        total_wa += s["with_wa"]

    if not lines:
        detail = "Aucun lead extrait hier."
    else:
        detail = "\n".join(lines)

    return (
        "[Message Auto]\n"
        f"📈 Récap scraping global — {yesterday.strftime('%d/%m/%Y')}\n\n"
        f"📊 Détail par niche :\n{detail}\n\n"
        f"🔢 Total leads extraits : {total_leads}\n"
        f"📧 Total emails : {total_email}\n"
        f"💬 Total WhatsApp : {total_wa}\n\n"
        f"✅ Villes scrapées : {scrape['done']}\n"
        f"❌ Erreurs : {scrape['error']}\n"
        f"⏳ Restant : {scrape['pending']}"
    )


# ── Main runner ───────────────────────────────────────────────────────────────


def run_daily_recap():
    supa = SupabaseService()
    wa = WasenderService()

    stats, yesterday = fetch_leads_by_niche(supa)
    scrape = fetch_scrape_progress(supa)

    msg = build_kilian_message(stats, scrape, yesterday)
    try:
        wa.send_whatsapp(KILIAN_NUMERO, msg)
        print(f"[INFO] Recap envoyé → Kilian ({KILIAN_NUMERO})")
    except Exception as exc:
        print(f"[ERROR] Échec envoi → Kilian: {exc}")


if __name__ == "__main__":
    run_daily_recap()
