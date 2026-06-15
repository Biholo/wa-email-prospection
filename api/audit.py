import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from core.auth import require_api_key
from core.rate_limit import check_rate_limit
from services.site_audit_service import SiteAuditService
from services.supabase_service import SupabaseService

router = APIRouter(tags=["audit"])


class AuditRequest(BaseModel):
    url: str
    max_pages: int = 1


class EmailCaptureRequest(BaseModel):
    email: str


class CompareRequest(BaseModel):
    urls: list[str]


# ---------------------------------------------------------------------------
# POST /api/audit
# ---------------------------------------------------------------------------

@router.post("/api/audit")
async def run_audit(body: AuditRequest, request: Request, _: str = Depends(require_api_key)):
    url = body.url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL invalide. Elle doit commencer par http:// ou https://")

    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Limite de 5 audits par heure atteinte depuis cette adresse IP. Réessayez dans une heure.")

    utm_source = request.query_params.get("utm_source")
    utm_campaign = request.query_params.get("utm_campaign")

    try:
        service = SiteAuditService()
        report = await asyncio.wait_for(
            service.run(url, utm_source=utm_source, utm_campaign=utm_campaign, max_pages=min(body.max_pages, 5)),
            timeout=60.0,
        )
        return report
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="L'audit a dépassé le délai de 60 secondes. Veuillez réessayer.") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'audit : {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/audit/compare
# ---------------------------------------------------------------------------

@router.post("/api/audit/compare")
async def compare_audits(body: CompareRequest, request: Request, _: str = Depends(require_api_key)):
    urls = [u.strip().rstrip("/") for u in body.urls if u.strip()]
    if len(urls) < 2:
        raise HTTPException(status_code=400, detail="Au moins 2 URLs requises pour la comparaison.")
    if len(urls) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 URLs pour la comparaison.")
    for u in urls:
        if not u.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail=f"URL invalide : {u}")

    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Limite de 5 audits par heure atteinte depuis cette adresse IP.")

    try:
        service = SiteAuditService()
        results = await asyncio.wait_for(
            asyncio.gather(*[service.run(u) for u in urls], return_exceptions=True),
            timeout=90.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout - certains sites sont trop lents à répondre.") from None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la comparaison : {exc}") from exc

    comparison = []
    for i, (url, result) in enumerate(zip(urls, results)):
        if isinstance(result, Exception):
            comparison.append({"url": url, "isClient": i == 0, "error": str(result)})
        else:
            comparison.append({
                "url": url,
                "domain": result.get("domain"),
                "isClient": i == 0,
                "globalScore": result.get("globalScore"),
                "grade": result.get("grade"),
                "cms": result.get("cms"),
                "techStack": result.get("techStack", {}),
                "messaging": result.get("messaging", {}),
                "freshness": result.get("freshness", {}),
                "acquisitionProfile": result.get("acquisitionProfile", {}),
                "marketingBudget": result.get("marketingBudget", {}),
                "internationalisation": result.get("internationalisation", {}),
                "socialPresence": result.get("socialPresence", {}),
                "targetKeyword": result.get("targetKeyword", ""),
                "screenshots": result.get("screenshots", {}),
                "blocks": result.get("blocks", {}),
                "topIssues": result.get("topIssues", []),
            })

    # Compute gaps: checks client fails/warns but at least one competitor passes
    gaps: list[dict] = []
    client = next((r for r in comparison if r.get("isClient") and "error" not in r), None)
    competitors = [r for r in comparison if not r.get("isClient") and "error" not in r]
    if client and competitors:
        for block_name, block_data in client.get("blocks", {}).items():
            for check in block_data.get("checks", []):
                if check.get("status") not in ("fail", "warning"):
                    continue
                pts_missed = check.get("maxPoints", 0) - check.get("points", 0)
                if pts_missed <= 0:
                    continue
                winning_competitors = []
                for comp in competitors:
                    comp_checks = comp.get("blocks", {}).get(block_name, {}).get("checks", [])
                    comp_check = next((c for c in comp_checks if c["id"] == check["id"]), None)
                    if comp_check and comp_check.get("status") == "pass":
                        winning_competitors.append(comp.get("domain", "?"))
                if winning_competitors:
                    gaps.append({
                        "block": block_name,
                        "checkId": check["id"],
                        "label": check["label"],
                        "clientStatus": check["status"],
                        "pointsMissed": pts_missed,
                        "competitors": winning_competitors,
                        "fix": check.get("fix", ""),
                    })
    gaps.sort(key=lambda g: g["pointsMissed"], reverse=True)

    # Opportunities: checks where ALL sites fail = first-mover advantage
    opportunities: list[dict] = []
    all_valid = [r for r in comparison if "error" not in r]
    if len(all_valid) >= 2:
        first = all_valid[0]
        for block_name, block_data in first.get("blocks", {}).items():
            if block_name == "ecommerce" and not any(
                r.get("blocks", {}).get("ecommerce", {}).get("isEcommerce") for r in all_valid
            ):
                continue
            if block_name == "localSeo" and not any(
                r.get("blocks", {}).get("localSeo", {}).get("isLocal") for r in all_valid
            ):
                continue
            for check in block_data.get("checks", []):
                pts = check.get("maxPoints", 0)
                if pts <= 0:
                    continue
                failing = sum(
                    1 for r in all_valid
                    if next(
                        (c for c in r.get("blocks", {}).get(block_name, {}).get("checks", [])
                         if c["id"] == check["id"]),
                        {}
                    ).get("status") in ("fail", "warning")
                )
                if failing == len(all_valid):
                    opportunities.append({
                        "block": block_name,
                        "checkId": check["id"],
                        "label": check["label"],
                        "pointsGainable": pts,
                        "failingCount": failing,
                        "message": "Aucun concurrent ne l'a - premier arrivé, premier servi",
                    })
    opportunities.sort(key=lambda o: o["pointsGainable"], reverse=True)

    # Competitive intel summary
    competitive_intel: dict = {}
    client_r = next((r for r in comparison if r.get("isClient") and "error" not in r), None)
    comps_valid = [r for r in comparison if not r.get("isClient") and "error" not in r]
    if client_r and comps_valid:
        client_score = client_r.get("globalScore", 0)
        comp_scores = [r.get("globalScore", 0) for r in comps_valid]
        avg_score = round(sum(comp_scores) / len(comp_scores))
        strongest = max(comps_valid, key=lambda r: r.get("globalScore", 0))
        weakest = min(comps_valid, key=lambda r: r.get("globalScore", 0))

        client_advantages = []
        for block_name, block_data in client_r.get("blocks", {}).items():
            for check in block_data.get("checks", []):
                if check.get("status") != "pass":
                    continue
                comps_failing = [
                    comp.get("domain", "?") for comp in comps_valid
                    if next(
                        (c for c in comp.get("blocks", {}).get(block_name, {}).get("checks", [])
                         if c["id"] == check["id"]),
                        {}
                    ).get("status") in ("fail", "warning")
                ]
                if comps_failing:
                    client_advantages.append({
                        "checkId": check["id"],
                        "label": check["label"],
                        "block": block_name,
                        "competitorsFailing": comps_failing,
                    })

        ranked = sorted(
            [(r.get("domain", "?"), r.get("globalScore", 0)) for r in all_valid],
            key=lambda x: x[1], reverse=True,
        )
        client_rank = next(
            (i + 1 for i, (d, _) in enumerate(ranked) if d == client_r.get("domain")), None
        )

        competitive_intel = {
            "clientRank": client_rank,
            "totalSites": len(ranked),
            "avgCompetitorScore": avg_score,
            "scoreGap": client_score - avg_score,
            "strongestCompetitor": strongest.get("domain"),
            "weakestCompetitor": weakest.get("domain"),
            "clientAdvantages": client_advantages[:10],
            "criticalGaps": [g["checkId"] for g in gaps[:3]],
        }

    # Matrix: {checkId: {domain: status}} for all checks across all sites
    matrix: dict[str, dict[str, str]] = {}
    for r in all_valid:
        dom = r.get("domain", "?")
        for block_data in r.get("blocks", {}).values():
            for check in block_data.get("checks", []):
                cid = check["id"]
                matrix.setdefault(cid, {})[dom] = check.get("status", "unavailable")

    return {
        "urls": urls,
        "results": comparison,
        "gaps": gaps,
        "opportunities": opportunities,
        "competitiveIntel": competitive_intel,
        "matrix": matrix,
    }


# ---------------------------------------------------------------------------
# PATCH /api/audit/{audit_id}/email   -  capture email + trigger follow-up
# ---------------------------------------------------------------------------

@router.patch("/api/audit/{audit_id}/email")
async def capture_audit_email(
    audit_id: str,
    body: EmailCaptureRequest,
    background_tasks: BackgroundTasks,
    _: str = Depends(require_api_key),
):
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Email invalide.")

    try:
        supa = SupabaseService()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Service Supabase indisponible.") from exc

    audit = supa.get_audit(audit_id)
    if not audit:
        raise HTTPException(status_code=404, detail="Audit introuvable.")

    supa.update_audit_email(audit_id, email)
    background_tasks.add_task(_send_audit_followup, audit, email)

    return {"ok": True, "audit_id": audit_id, "email": email}


# ---------------------------------------------------------------------------
# Background task  -  send follow-up email via Resend + log to Supabase
# ---------------------------------------------------------------------------

def _send_audit_followup(audit: dict, email: str) -> None:
    try:
        from services.resend_service import ResendService
        supa = SupabaseService()
        resend = ResendService()

        domain = audit.get("domain", "votre site")
        score = audit.get("global_score", 0)
        grade = audit.get("grade", "FAIR")
        audit_id = audit.get("id", "")

        subject = f"Votre audit de {domain}  -  Score {score}/100"
        html = _build_audit_email_html(domain, score, grade, audit_id, email)

        result = resend.send_email(to=email, subject=subject, html=html)
        resend_id = result.get("id")

        now = datetime.now(timezone.utc).isoformat()
        supa.log_email({
            "audit_id": audit_id,
            "email": email,
            "domain": domain,
            "subject": subject,
            "template": "audit_rapport_j0",
            "step": 1,
            "resend_id": resend_id,
            "status": "sent",
            "sent_at": now,
        })
        print(f"[AUDIT EMAIL] Envoyé -> {email} | resend_id={resend_id}")
    except Exception as exc:
        print(f"[AUDIT EMAIL] Erreur: {exc}")


def _build_audit_email_html(domain: str, score: int, grade: str, audit_id: str, email: str) -> str:
    grade_colors = {
        "EXCELLENT": "#22c55e",
        "GOOD": "#84cc16",
        "FAIR": "#f59e0b",
        "CRITICAL": "#ef4444",
    }
    grade_labels = {
        "EXCELLENT": "Excellent",
        "GOOD": "Bon",
        "FAIR": "À améliorer",
        "CRITICAL": "Critique",
    }
    color = grade_colors.get(grade, "#6b7280")
    label = grade_labels.get(grade, grade)
    report_url = f"https://audit.develly.fr/rapport/{audit_id}"

    return f"""<!DOCTYPE html>
<html lang="fr">
<body style="margin:0;padding:0;background:#f4f4f5;font-family:Arial,sans-serif;">
  <div style="max-width:580px;margin:32px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">
    <div style="background:#0f172a;padding:24px 32px;">
      <p style="color:#fff;margin:0;font-size:20px;font-weight:700;">Develly Audit</p>
    </div>
    <div style="padding:32px;">
      <h1 style="margin:0 0 8px;font-size:22px;color:#0f172a;">Votre rapport est prêt</h1>
      <p style="margin:0 0 24px;color:#64748b;">Résultats de l'audit pour <strong>{domain}</strong></p>
      <div style="background:{color};border-radius:10px;padding:24px;text-align:center;margin-bottom:24px;">
        <p style="margin:0;color:#fff;font-size:52px;font-weight:800;line-height:1;">{score}</p>
        <p style="margin:6px 0 0;color:rgba(255,255,255,.85);font-size:15px;">/ 100  -  {label}</p>
      </div>
      <p style="color:#374151;line-height:1.6;">Nous avons analysé <strong>{domain}</strong> sur <strong>5 axes</strong> : Performance, SEO, Légal &amp; RGPD, Conversion et Mobile.</p>
      <p style="text-align:center;margin:28px 0;">
        <a href="{report_url}"
           style="display:inline-block;background:#0f172a;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;">
          Voir mon rapport complet
        </a>
      </p>
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
      <p style="color:#94a3b8;font-size:12px;line-height:1.6;">
        Vous recevez cet email car vous avez demandé un audit sur audit.develly.fr.<br>
        <a href="https://audit.develly.fr/unsubscribe?email={email}" style="color:#94a3b8;">Se désabonner</a>
      </p>
    </div>
  </div>
</body>
</html>"""
