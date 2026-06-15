#!/usr/bin/env python3
"""CLI to test the audit endpoint locally."""

import argparse
import json
import os
import re
import sys
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def _save_json(data: dict | list, prefix: str, label: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    slug = re.sub(r"[^\w.-]", "_", label)[:40].strip("_")
    filename = f"{prefix}_{date}_{slug}.json"
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path

BASE_URL = "http://localhost:8000"
_API_KEY = os.getenv("API_SECRET_KEY", "")
_HEADERS = {"Authorization": f"Bearer {_API_KEY}"} if _API_KEY else {}

GRADE_COLORS = {
    "EXCELLENT": "\033[92m",
    "GOOD": "\033[93m",
    "FAIR": "\033[33m",
    "CRITICAL": "\033[91m",
}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
STATUS_ICONS = {"pass": "✅", "fail": "❌", "warning": "⚠️ ", "unavailable": "⬜"}


def _color(grade: str) -> str:
    return GRADE_COLORS.get(grade, "")


def cmd_audit(args):
    print(f"\n{BOLD}Audit de {args.url}...{RESET}\n")
    try:
        resp = httpx.post(
            f"{BASE_URL}/api/audit",
            json={"url": args.url, "max_pages": args.pages},
            headers=_HEADERS,
            timeout=90,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"❌ Erreur HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError:
        print("❌ Serveur non joignable  -  lance d'abord: uvicorn main:app --reload")
        sys.exit(1)

    data = resp.json()

    # Always save full JSON to output/
    domain = data.get("domain", args.url.replace("https://", "").replace("http://", ""))
    out_path = _save_json(data, "audit", domain)
    print(f"{DIM}JSON saved -> {out_path}{RESET}\n")

    if args.raw:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    # Header
    score = data.get("globalScore", 0)
    grade = data.get("grade", "")
    domain = data.get("domain", "")
    cms = data.get("cms", "")
    col = _color(grade)
    print(f"{BOLD}{col}{'=' * 52}{RESET}")
    print(f"{BOLD}{col}  {domain}   -   {score}/120  [{grade}]{RESET}")
    if cms and cms != "Inconnu":
        print(f"{col}  CMS: {cms}{RESET}")
    print(f"{col}{'=' * 52}{RESET}")
    print(f"{DIM}  {data.get('summary', '')}{RESET}\n")

    # Intelligence signals
    msg = data.get("messaging", {})
    fresh = data.get("freshness", {})
    acq = data.get("acquisitionProfile", {})
    budget = data.get("marketingBudget", {})
    intl = data.get("internationalisation", {})

    if msg.get("h1") or msg.get("metaDescription"):
        print(f"{BOLD}PROPOSITION DE VALEUR{RESET}")
        print(f"  H1            {msg.get('h1', '-')[:80]}")
        print(f"  Meta desc.    {msg.get('metaDescription', '-')[:80]}")
        print()

    if acq or budget:
        print(f"{BOLD}ACQUISITION & BUDGET{RESET}")
        if acq:
            channels_str = ", ".join(acq.get("channels", [])) or "-"
            print(f"  Canaux        {channels_str}  [{acq.get('level', '-')}]")
        if budget:
            signals_str = ", ".join(budget.get("signals", [])) or "aucun signal"
            print(f"  Budget mktg   {budget.get('level', '-')} ({budget.get('score', 0)}/10)  -  {signals_str}")
        if intl.get("isMultilingual"):
            langs_str = ", ".join(intl.get("hreflang", [])) or "via " + ", ".join(intl.get("i18nTools", []))
            print(f"  International {langs_str}")
        if fresh:
            freshness_parts = []
            if fresh.get("copyrightYear"):
                freshness_parts.append(f"© {fresh['copyrightYear']}")
            if fresh.get("hasBlog"):
                freshness_parts.append("blog présent")
            if fresh.get("lastModified"):
                freshness_parts.append(f"modifié {fresh['lastModified'][:10]}")
            if freshness_parts:
                print(f"  Fraîcheur     {', '.join(freshness_parts)}")
        print()

    # Social presence
    social = data.get("socialPresence", {})
    social_found = {k: v for k, v in social.items() if v and k not in ("score", "total")}
    if social_found:
        print(f"{BOLD}PRESENCE SOCIALE{RESET}")
        for platform, url_val in social_found.items():
            print(f"  {platform:<12} {url_val[:70]}")
        print()

    # Security score
    sec_block = data.get("blocks", {}).get("security", {})
    sec_score = sec_block.get("securityScore")
    if sec_score is not None:
        sec_col = "\033[92m" if sec_score >= 80 else ("\033[93m" if sec_score >= 50 else "\033[91m")
        print(f"{BOLD}SECURITE HEADERS{RESET}  {sec_col}{sec_score}/100{RESET}")
        for ch in sec_block.get("checks", []):
            if ch.get("status") in ("fail", "warning"):
                icon = STATUS_ICONS.get(ch["status"], "?")
                print(f"  {icon} {ch.get('label', '')}  {DIM}{ch.get('value', '')}{RESET}")
        print()

    # Screenshots
    shots = data.get("screenshots", {})
    if shots:
        print(f"{BOLD}SCREENSHOTS{RESET}")
        for device, path in shots.items():
            print(f"  {device:<8} {path}")
        print()

    # Tech stack
    tech_stack = data.get("techStack", {})
    if tech_stack:
        print(f"{BOLD}TECH STACK{RESET}")
        for cat, techs in tech_stack.items():
            cat_short = cat[:18]
            print(f"  {cat_short:<18} {', '.join(techs)}")
        print()

    # Scores par bloc
    blocks = data.get("blocks", {})
    print(f"{BOLD}SCORES PAR BLOC{RESET}")
    _BONUS_SCORE_KEYS = {
        "security": "securityScore",
        "localSeo": "localScore",
        "ecommerce": "ecommerceScore",
    }
    for name, block in blocks.items():
        if block.get("maxScore", 0) == 0:
            bonus_key = _BONUS_SCORE_KEYS.get(name)
            bonus_val = block.get(bonus_key, 0) if bonus_key else 0
            # Skip informative blocks that add no value when at 0 and not applicable
            if name == "ecommerce" and not block.get("isEcommerce"):
                continue
            if name == "localSeo" and not block.get("isLocal"):
                continue
            label = name[:14]
            bonus_col = "\033[92m" if bonus_val >= 80 else ("\033[93m" if bonus_val >= 50 else "\033[91m")
            print(f"  {label:<14} {bonus_col}{bonus_val:>3}/100{RESET}  (bonus)")
            continue
        s = block.get("score", 0)
        mx = block.get("maxScore", 0)
        bar_filled = round(s / mx * 20) if mx else 0
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        print(f"  {name:<14} {s:>2}/{mx}  {bar}")

    # Top issues
    issues = data.get("topIssues", [])
    if issues:
        print(f"\n{BOLD}TOP ISSUES{RESET}")
        for issue in issues:
            sev = issue.get("severity", "").upper()
            label = issue.get("label", "")
            impact = issue.get("impact", "")
            fix = issue.get("fix", "")
            blk = issue.get("block", "")
            print(f"\n  ❌ [{sev}] {BOLD}{label}{RESET}  {DIM}({blk}){RESET}")
            print(f"     Impact : {impact}")
            print(f"     Fix    : {fix}")

    # Tous les problèmes (fail + warning)
    if args.issues or args.detail:
        only_issues = args.issues and not args.detail
        print(f"\n{BOLD}{'PROBLÈMES DÉTECTÉS' if only_issues else 'DÉTAIL COMPLET'}{RESET}")
        for name, block in blocks.items():
            checks = block.get("checks", [])
            if not checks:
                continue
            filtered = [c for c in checks if c.get("status") in ("fail", "warning")] if only_issues else checks
            if not filtered:
                continue
            geo_label = f"  (geoScore={block.get('geoScore',0)}/100)" if "geoScore" in block else ""
            print(f"\n{BOLD}-- {name.upper()}{geo_label} --{RESET}")
            for ch in filtered:
                icon = STATUS_ICONS.get(ch.get("status", ""), "?")
                pts = ch.get("points", 0)
                mx = ch.get("maxPoints", 0)
                label = ch.get("label", "")
                val = ch.get("value", "")
                sev = ch.get("severity", "")
                impact = ch.get("impact", "")
                fix = ch.get("fix", "")
                score_str = f"{pts}/{mx}" if mx > 0 else "info"
                print(f"  {icon} [{sev.upper()}] {BOLD}{label}{RESET}  {DIM}{score_str}{RESET}")
                print(f"       Valeur  : {val}")
                if ch.get("status") in ("fail", "warning"):
                    print(f"       Impact  : {impact}")
                    print(f"       Fix     : {fix}")

    # Pages multi-page audit
    pages = data.get("pages", [])
    if pages:
        print(f"\n{BOLD}PAGES ANALYSÉES ({len(pages)}){RESET}")
        for page in pages:
            if "error" in page and "score" not in page:
                print(f"\n  ❌ {page.get('url', '?')}")
                print(f"     {page['error']}")
            else:
                p_score = page.get("score", 0)
                p_max = page.get("maxScore", 15)
                p_title = page.get("title", "-")[:50]
                bar_filled = round(p_score / p_max * 15) if p_max else 0
                bar = "█" * bar_filled + "░" * (15 - bar_filled)
                print(f"\n  {BOLD}{p_title}{RESET}")
                print(f"  {DIM}{page.get('url', '')}{RESET}")
                print(f"  SEO: {p_score}/{p_max}  {bar}")
                for ch in page.get("checks", []):
                    if ch.get("status") in ("fail", "warning"):
                        icon = STATUS_ICONS.get(ch["status"], "?")
                        print(f"    {icon} {ch.get('label', '')}  {DIM}{ch.get('value', '')}{RESET}")

    print(f"\n{DIM}analyzedAt: {data.get('analyzedAt', '')}{RESET}\n")


def cmd_email(args):
    print(f"\nCapture email {args.email} pour audit {args.audit_id}...")
    try:
        resp = httpx.patch(
            f"{BASE_URL}/api/audit/{args.audit_id}/email",
            json={"email": args.email},
            headers=_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        print(f"✅ {resp.json()}")
    except httpx.HTTPStatusError as e:
        print(f"❌ Erreur {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError:
        print("❌ Serveur non joignable.")
        sys.exit(1)


def cmd_compare(args):
    urls = args.urls
    print(f"\n{BOLD}Comparaison de {len(urls)} sites...{RESET}\n")
    try:
        resp = httpx.post(
            f"{BASE_URL}/api/audit/compare",
            json={"urls": urls},
            headers=_HEADERS,
            timeout=120,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"❌ Erreur HTTP {e.response.status_code}: {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError:
        print("❌ Serveur non joignable - lance: uvicorn main:app --reload")
        sys.exit(1)

    data = resp.json()

    # Always save full JSON to output/
    label = "_vs_".join(u.replace("https://", "").replace("http://", "").split("/")[0] for u in urls[:3])
    out_path = _save_json(data, "compare", label)
    print(f"{DIM}JSON saved -> {out_path}{RESET}\n")

    if args.raw:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    results = data.get("results", [])
    if not results:
        print("Aucun résultat.")
        return

    # ---- Scores table ----
    col_w = 24
    header = (
        f"{'Site':<{col_w}} {'Score':>6} {'CMS':<14}"
        f" {'Perf':>5} {'SEO':>5} {'Legal':>6} {'Conv':>5} {'Mob':>5} {'GEO':>5}"
    )
    print(f"{BOLD}SCORES{RESET}")
    print(header)
    print("-" * len(header))

    for r in results:
        if "error" in r and "globalScore" not in r:
            print(f"  ❌ {r.get('url', '?')[:col_w]}  ERREUR: {r['error'][:40]}")
            continue
        domain = (r.get("domain") or r.get("url", "?"))[:col_w]
        score = r.get("globalScore", 0)
        grade = r.get("grade", "")
        cms = (r.get("cms") or "?")[:14]
        col = _color(grade)
        blocks = r.get("blocks", {})
        perf = blocks.get("performance", {})
        seo = blocks.get("seo", {})
        legal = blocks.get("legal", {})
        conv = blocks.get("conversion", {})
        mob = blocks.get("mobile", {})
        geo = blocks.get("geo", {})
        marker = f"  {BOLD}[CLIENT]{RESET}" if r.get("isClient") else ""
        print(
            f"{col}{domain:<{col_w}}{RESET}"
            f" {col}{score:>3}/120{RESET}"
            f" {cms:<14}"
            f" {perf.get('score', '?'):>2}/{perf.get('maxScore', 25)}"
            f" {seo.get('score', '?'):>2}/{seo.get('maxScore', 25)}"
            f" {legal.get('score', '?'):>2}/{legal.get('maxScore', 20)}"
            f" {conv.get('score', '?'):>2}/{conv.get('maxScore', 20)}"
            f" {mob.get('score', '?'):>2}/{mob.get('maxScore', 10)}"
            f" {geo.get('score', '?'):>2}/{geo.get('maxScore', 20)}"
            + marker
        )

    # ---- Tech stack comparison ----
    all_categories: list[str] = []
    for r in results:
        for cat in r.get("techStack", {}).keys():
            if cat not in all_categories:
                all_categories.append(cat)

    if all_categories:
        print(f"\n{BOLD}TECH STACK{RESET}")
        cat_w = 22
        domains_short = [(r.get("domain") or "?")[:18] for r in results]
        header_ts = f"  {'Catégorie':<{cat_w}}" + "".join(f" {d:<22}" for d in domains_short)
        print(header_ts)
        print("  " + "-" * (len(header_ts) - 2))
        for cat in all_categories:
            row = f"  {cat:<{cat_w}}"
            for r in results:
                techs = r.get("techStack", {}).get(cat, [])
                cell = ", ".join(techs)[:21] if techs else "-"
                row += f" {cell:<22}"
            print(row)

    # ---- Messaging comparison (H1 + meta description) ----
    has_messaging = any(r.get("messaging") for r in results if "error" not in r)
    if has_messaging:
        print(f"\n{BOLD}PROPOSITION DE VALEUR{RESET}")
        for r in results:
            if "error" in r and "globalScore" not in r:
                continue
            domain_short = (r.get("domain") or "?")[:30]
            marker = f" {BOLD}[CLIENT]{RESET}" if r.get("isClient") else ""
            msg = r.get("messaging", {})
            h1 = msg.get("h1", "-")[:70]
            acq = r.get("acquisitionProfile", {})
            budget = r.get("marketingBudget", {})
            channels = ", ".join(acq.get("channels", [])) or "-"
            bgt = f"{budget.get('level', '-')} ({budget.get('score', 0)}/10)"
            intl = r.get("internationalisation", {})
            multilang = ("oui - " + ", ".join(intl.get("hreflang", []))) if intl.get("isMultilingual") else "non"
            print(f"\n  {BOLD}{domain_short}{RESET}{marker}")
            print(f"    H1       {h1}")
            print(f"    Canaux   {channels}  |  Budget {bgt}  |  Intl: {multilang}")

    # ---- Freshness comparison ----
    has_freshness = any(r.get("freshness") for r in results if "error" not in r)
    if has_freshness:
        print(f"\n{BOLD}FRAICHEUR DES SITES{RESET}")
        header_fr = f"  {'Site':<28} {'Copyright':>10} {'Blog':>6} {'Modifié':<20}"
        print(header_fr)
        print("  " + "-" * (len(header_fr) - 2))
        for r in results:
            if "error" in r and "globalScore" not in r:
                continue
            domain_short = (r.get("domain") or "?")[:28]
            fresh = r.get("freshness", {})
            cp_yr = str(fresh.get("copyrightYear") or "-")
            blog = "oui" if fresh.get("hasBlog") else "non"
            modif = (fresh.get("lastModified") or "-")[:19]
            marker = " [C]" if r.get("isClient") else "    "
            print(f"  {domain_short:<28} {cp_yr:>10} {blog:>6} {modif:<20}{marker}")

    # ---- Gaps: what client is missing vs competitors ----
    gaps = data.get("gaps", [])
    if gaps:
        print(f"\n{BOLD}AVANTAGES CONCURRENTS (points perdus par le client){RESET}")
        print(f"  {'Points':>6}  {'Bloc':<12} {'Check':<36} {'Qui a ca'}")
        print("  " + "-" * 78)
        for g in gaps:
            pts = g.get("pointsMissed", 0)
            blk = g.get("block", "")[:12]
            label = g.get("label", "")[:36]
            who = ", ".join(g.get("competitors", []))[:20]
            print(f"  \033[91m-{pts:>5}pt\033[0m  {blk:<12} {label:<36} {DIM}{who}{RESET}")
        print()
        print(f"{BOLD}PLAN D'ACTION (top 5){RESET}")
        for i, g in enumerate(gaps[:5], 1):
            fix = g.get("fix", "")
            label = g.get("label", "")
            pts = g.get("pointsMissed", 0)
            print(f"  {i}. [{g['block']}] {label} (+{pts}pts possibles)")
            if fix:
                print(f"     {DIM}{fix}{RESET}")

    # ---- Opportunities inversées (tous les sites échouent) ----
    opportunities = data.get("opportunities", [])
    if opportunities:
        print(f"\n{BOLD}OPPORTUNITES FIRST-MOVER (personne ne l'a encore){RESET}")
        print(f"  {'Pts':>4}  {'Bloc':<12} {'Check'}")
        print("  " + "-" * 60)
        for opp in opportunities[:8]:
            pts = opp.get("pointsGainable", 0)
            blk = opp.get("block", "")[:12]
            label = opp.get("label", "")[:50]
            print(f"  \033[92m+{pts:>3}pt\033[0m  {blk:<12} {label}")

    # ---- Competitive intel summary ----
    intel = data.get("competitiveIntel", {})
    if intel:
        print(f"\n{BOLD}SYNTHESE CONCURRENTIELLE{RESET}")
        rank = intel.get("clientRank")
        total = intel.get("totalSites")
        gap = intel.get("scoreGap", 0)
        gap_str = f"+{gap}" if gap >= 0 else str(gap)
        gap_col = "\033[92m" if gap >= 0 else "\033[91m"
        print(f"  Rang client   {rank}/{total}  |  Ecart vs moyenne : {gap_col}{gap_str}pts{RESET}")
        print(f"  Plus fort     {intel.get('strongestCompetitor', '-')}")
        print(f"  Plus faible   {intel.get('weakestCompetitor', '-')}")
        advantages = intel.get("clientAdvantages", [])
        if advantages:
            print(f"  Avantages client ({len(advantages)}) : " + ", ".join(a["label"][:30] for a in advantages[:3]))

    # ---- Keyword targeting comparison ----
    has_kw = any(r.get("targetKeyword") for r in results if "error" not in r)
    if has_kw:
        print(f"\n{BOLD}MOT-CLE CIBLE{RESET}")
        for r in results:
            if "error" in r and "globalScore" not in r:
                continue
            domain_short = (r.get("domain") or "?")[:28]
            kw = r.get("targetKeyword") or "-"
            marker = " [CLIENT]" if r.get("isClient") else ""
            print(f"  {domain_short:<28} {kw[:60]}{marker}")

    # ---- Social presence comparison ----
    has_social = any(r.get("socialPresence", {}).get("score", 0) > 0 for r in results if "error" not in r)
    if has_social:
        platforms = list(_SOCIAL_KEYS)
        print(f"\n{BOLD}PRESENCE SOCIALE{RESET}")
        header_s = f"  {'Site':<28}" + "".join(f" {p[:9]:<10}" for p in platforms)
        print(header_s)
        print("  " + "-" * (len(header_s) - 2))
        for r in results:
            if "error" in r and "globalScore" not in r:
                continue
            soc = r.get("socialPresence", {})
            domain_short = (r.get("domain") or "?")[:28]
            cells = "".join(
                f" {'oui':<10}" if soc.get(p) else f" {'-':<10}"
                for p in platforms
            )
            marker = " [C]" if r.get("isClient") else ""
            print(f"  {domain_short:<28}{cells}{marker}")

    # ---- Security comparison ----
    has_sec = any(r.get("blocks", {}).get("security", {}).get("securityScore") is not None
                  for r in results if "error" not in r)
    if has_sec:
        print(f"\n{BOLD}SECURITE HEADERS{RESET}")
        for r in results:
            if "error" in r and "globalScore" not in r:
                continue
            sec = r.get("blocks", {}).get("security", {})
            score_s = sec.get("securityScore", 0)
            domain_short = (r.get("domain") or "?")[:28]
            sec_col = "\033[92m" if score_s >= 80 else ("\033[93m" if score_s >= 50 else "\033[91m")
            marker = " [CLIENT]" if r.get("isClient") else ""
            print(f"  {domain_short:<28} {sec_col}{score_s:>3}/100{RESET}{marker}")

    # ---- Screenshots ----
    has_shots = any(r.get("screenshots") for r in results if "error" not in r)
    if has_shots:
        print(f"\n{BOLD}SCREENSHOTS{RESET}")
        for r in results:
            shots = r.get("screenshots", {})
            if not shots:
                continue
            domain_short = (r.get("domain") or "?")[:28]
            for device, path in shots.items():
                print(f"  {domain_short:<28} [{device}] {path}")

    print()


_SOCIAL_KEYS = list({"linkedin", "facebook", "instagram", "tiktok", "youtube", "twitter", "pinterest"})


def main():
    parser = argparse.ArgumentParser(description="Develly Audit CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # audit
    p_audit = sub.add_parser("audit", help="Lancer un audit de site")
    p_audit.add_argument("url", help="URL à auditer (ex: https://develly.io)")
    p_audit.add_argument("--raw", action="store_true", help="Afficher le JSON brut")
    p_audit.add_argument("--detail", "-d", action="store_true", help="Afficher tous les checks")
    p_audit.add_argument("--issues", "-i", action="store_true", help="Afficher uniquement les fail + warning")
    p_audit.add_argument("--pages", type=int, default=1, metavar="N",
                         help="Nombre de pages à auditer depuis la nav (1-5, défaut: 1)")
    p_audit.set_defaults(func=cmd_audit)

    # email
    p_email = sub.add_parser("email", help="Capturer un email pour un audit existant")
    p_email.add_argument("audit_id", help="UUID de l'audit")
    p_email.add_argument("email", help="Email à capturer")
    p_email.set_defaults(func=cmd_email)

    # compare
    p_compare = sub.add_parser("compare", help="Comparer plusieurs sites (1er = client)")
    p_compare.add_argument("urls", nargs="+",
                           help="URLs à comparer - la 1ère est le client (ex: https://client.fr https://concurrent.fr)")
    p_compare.add_argument("--raw", action="store_true", help="Afficher le JSON brut")
    p_compare.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
