import random
import time
from datetime import date, timedelta
from pathlib import Path

import yaml

from core.db import log_entry
from core.state import increment_errors, increment_processed
from logic.angle_detector import AngleDetector
from logic.competitor_resolver import CompetitorResolver
from logic.message_builder import MessageBuilder
from services.brevo_service import BrevoService
from services.pagespeed_service import PageSpeedService


def run_email_pipeline(
    config_name: str,
    config: dict,
    brevo: BrevoService,
    pagespeed: PageSpeedService,
    angle_detector: AngleDetector,
    competitor_resolver: CompetitorResolver,
    message_builder: MessageBuilder,
    dry_run: bool = False,
) -> None:
    workflow = config.get("workflow", {}).get("email", {})
    source_list_id: int = int(workflow["source_list_id"])
    target_list_id: int = int(workflow["target_list_id"])
    max_par_jour: int = int(workflow.get("max_per_day", 200))
    delai: int = int(config.get("rules", {}).get("send_delay", 30))

    company_cfg = config.get("company", {})
    proprietaire: str = company_cfg.get("owner", "")

    tag = "[DRY RUN] " if dry_run else ""

    contacts = brevo.get_contacts(
        liste_id=source_list_id,
        brevo_filter=f'equals(PROPRIETAIRE,"{proprietaire}")' if proprietaire else None,
        attr_not_equals={"CATEGORIE": "Expert-comptable"},
    )

    print(f"{tag}EMAIL — {len(contacts)} contact(s) dans liste #{source_list_id}")

    city_spacing_days: int = int(config.get("rules", {}).get("city_spacing_days", 30))
    one_month_ago = (date.today() - timedelta(days=city_spacing_days)).isoformat()
    recently_sent = brevo.get_contacts(
        liste_id=source_list_id,
        brevo_filter=f'equals(PROPRIETAIRE,"{proprietaire}")' if proprietaire else None,
        attr_gte={"TOTAL_MESSAGE_ENVOYE": 1, "DATE_DERNIERE_ENVOI": one_month_ago},
    )
    blocked_cities: set = {
        c.get("attributes", {}).get("CITY_ID", "")
        for c in recently_sent
        if c.get("attributes", {}).get("CITY_ID")
    }
    random.shuffle(contacts)
    if blocked_cities:
        print(f"{tag}EMAIL — {len(blocked_cities)} ville(s) bloquée(s) (envoi < {city_spacing_days}j)")

    sent = 0
    dry_run_records: list = []
    seen_cities_this_run: set = set()

    for contact in contacts:
        if not dry_run and sent >= max_par_jour:
            print(f"{tag}EMAIL — limite journalière atteinte ({max_par_jour})")
            break

        attrs = contact.get("attributes", {})
        email: str = contact.get("email", "") or attrs.get("EMAIL", "") or ""
        company: str = attrs.get("COMPANY", "")
        city_id: str = attrs.get("CITY_ID", "")
        niche: str = attrs.get("CATEGORIE", "")
        site_url: str = attrs.get("WEBSITE_URL", "") or ""

        if niche == "Expert-comptable":
            continue

        log_record: dict = {
            "config": config_name,
            "liste_id": source_list_id,
            "contact_email": email,
            "contact_company": company,
            "canal": "EMAIL",
        }

        try:
            if city_id and (city_id in blocked_cities or city_id in seen_cities_this_run):
                print(f"{tag}  {company} ({email}) → SKIPPÉ (ville déjà envoyée récemment)")
                log_record.update(status="SKIPPÉ", erreur_detail=f"Ville {city_id} déjà contactée récemment")
                log_entry(log_record)
                increment_processed()
                continue

            if city_id:
                seen_cities_this_run.add(city_id)

            is_facebook = bool(site_url and "facebook.com" in site_url.lower())

            if is_facebook:
                pagespeed_score = None
            else:
                ps_result = pagespeed.analyse(site_url)
                pagespeed_score = ps_result.get("score")
            log_record["pagespeed_score"] = pagespeed_score

            lead_note = float(attrs.get("AVERAGE_RATE") or 0)
            rep_candidates = competitor_resolver.get_reputation_candidates(
                city_id=city_id,
                niche=niche,
                lead_email=email,
                lead_note=lead_note,
            )
            print(f"{tag}  {company} ({email}) — {len(rep_candidates)} candidat(s) réputation | note lead={lead_note}")

            if is_facebook:
                angle = "INVISIBILITÉ"
            else:
                angle = angle_detector.detect(
                    contact, pagespeed_score,
                    reputation_candidates=rep_candidates,
                    exclude_angles=["INVISIBILITÉ"],
                )
                if angle is None:
                    reason = angle_detector.explain_skip(
                        contact, pagespeed_score,
                        reputation_candidates=rep_candidates,
                        exclude_angles=["INVISIBILITÉ"],
                    )
                    brevo.move_to_list(email, source_list_id, 27)
                    print(f"{tag}  {company} ({email}) → HORS SCOPE | {reason}")
                    log_record.update(status="HORS SCOPE", erreur_detail=f"Aucun angle — {reason}")
                    log_entry(log_record)
                    increment_processed()
                    continue

            log_record["angle"] = angle

            concurrent_data = competitor_resolver.get_concurrents(
                angle=angle,
                city_id=city_id,
                niche=niche,
                lead_email=email,
                lead_note=float(attrs.get("AVERAGE_RATE") or 0),
                lead_avis=int(attrs.get("NUMBER_OF_RATE") or 0),
            )
            log_record["concurrent_1"] = concurrent_data["concurrent_1"]["nom"]
            log_record["concurrent_2"] = concurrent_data["concurrent_2"]["nom"]

            messages = message_builder.build_all(
                contact=contact,
                angle=angle,
                concurrent_data=concurrent_data,
                combos_filter=["email_1", "email_2", "email_3"],
            )

            if messages.get("__skip__"):
                if email:
                    competitor_resolver.archive_lead(email)
                brevo.move_to_list(email, source_list_id, 27)
                print(f"{tag}  {company} ({email}) → HORS SCOPE | activité hors niche (SKIP)")
                log_record.update(status="HORS SCOPE", erreur_detail="SKIP — activité hors niche")
                log_entry(log_record)
                increment_processed()
                continue

            if dry_run:
                e1 = messages["email_1"]
                preview = f"EMAIL_1: {e1['objet']} || {e1['corps'][:200]}"
                print(f"{tag}  {company} ({email}) | {angle} → DRY_RUN")
                log_record.update(status="DRY_RUN", erreur_detail=preview)
                dry_run_records.append({
                    "email": email,
                    "prenom": attrs.get("FIRSTNAME", "") or attrs.get("PRENOM", ""),
                    "company": company,
                    "niche": niche,
                    "ville": concurrent_data.get("ville_nom", city_id),
                    "proprietaire": attrs.get("PROPRIETAIRE", ""),
                    "angle": angle,
                    "concurrent_1": {"nom": concurrent_data["concurrent_1"]["nom"], "note": concurrent_data["concurrent_1"]["note"], "nb_avis": concurrent_data["concurrent_1"]["nb_avis"]},
                    "concurrent_2": {"nom": concurrent_data["concurrent_2"]["nom"], "note": concurrent_data["concurrent_2"]["note"], "nb_avis": concurrent_data["concurrent_2"]["nb_avis"]},
                    "email_1": {"objet": messages["email_1"]["objet"], "corps": messages["email_1"]["corps"]},
                    "email_2": {"objet": messages["email_2"]["objet"], "corps": messages["email_2"]["corps"]},
                    "email_3": {"objet": messages["email_3"]["objet"], "corps": messages["email_3"]["corps"]},
                })
                log_entry(log_record)
                increment_processed()
                sent += 1
                time.sleep(1)
                continue

            today = date.today().isoformat()
            prochaine_relance = (date.today() + timedelta(days=3)).isoformat()

            brevo.update_contact(
                email,
                {
                    "ANGLE": angle,
                    "CANAL_PRINCIPAL": "EMAIL",
                    "DATE_DERNIERE_ENVOI": today,
                    "TOTAL_MESSAGE_ENVOYE": 1,
                    "PROCHAINE_RELANCE": prochaine_relance,
                    "COMMENTAIRE": f"Emails générés le {today} | Angle: {angle}",
                    "EMAIL_1_OBJET": messages["email_1"]["objet"],
                    "EMAIL_1_CORPS": messages["email_1"]["corps"],
                    "EMAIL_2_OBJET": messages["email_2"]["objet"],
                    "EMAIL_2_CORPS": messages["email_2"]["corps"],
                    "EMAIL_3_OBJET": messages["email_3"]["objet"],
                    "EMAIL_3_CORPS": messages["email_3"]["corps"],
                },
            )

            brevo.move_to_list(email, source_list_id, target_list_id)

            print(f"  {company} ({email}) → OK | {angle} | PageSpeed={pagespeed_score}")
            log_record["status"] = "ENVOYÉ"
            log_entry(log_record)
            increment_processed()
            sent += 1
            time.sleep(delai)

        except Exception as exc:
            print(f"{tag}  {company} ({email}) → ERREUR : {exc}")
            log_record.update(status="ERREUR", erreur_detail=str(exc))
            log_entry(log_record)
            increment_errors()
            increment_processed()

    if dry_run and dry_run_records:
        output_path = Path(f"output/dry_run_email_{config_name}_{date.today().isoformat()}.yaml")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml.dump(dry_run_records, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"{tag}EMAIL — résultats écrits dans {output_path}")

    print(f"{tag}EMAIL — terminé. {sent} traité(s).")
