import random
import time
from datetime import date, timedelta
from pathlib import Path

import yaml

from core.db import log_entry
from core.holidays import is_sending_day
from core.state import increment_errors, increment_processed
from logic.angle_detector import AngleDetector
from logic.competitor_resolver import CompetitorResolver
from logic.message_builder import MessageBuilder
from services.brevo_service import BrevoService
from services.wasender_service import WasenderService
from services.pagespeed_service import PageSpeedService


TEST_LIST_ID = 28


def run_whatsapp_pipeline(
    config_name: str,
    config: dict,
    brevo: BrevoService,
    wasender: WasenderService,
    pagespeed: PageSpeedService,
    angle_detector: AngleDetector,
    competitor_resolver: CompetitorResolver,
    message_builder: MessageBuilder,
    dry_run: bool = False,
    mode: str = "all",  # "all" | "nouveaux" | "relances"
    test_phone: str = "",  # si renseigné : envoie wa_1 à ce numéro, mode dry_run pour tout le reste
    test_mode: bool = False,  # lit depuis liste 28, actions réelles vers les autres listes
) -> None:
    workflow = config.get("workflow", {}).get("whatsapp", {})
    source_list_id: int = TEST_LIST_ID if test_mode else int(workflow["source_list_id"])
    max_par_jour: int = int(workflow.get("max_per_day", 100))
    delai: int = int(config.get("rules", {}).get("send_delay", 30))

    company_cfg = config.get("company", {})
    proprietaire: str = company_cfg.get("owner", "")

    if test_phone:
        dry_run = True  # test_phone implique dry_run pour tous les appels Brevo/Supabase

    today = date.today()
    today_str = today.isoformat()
    tag = "[TEST] " if test_mode else ("[DRY RUN] " if dry_run else "")

    if not is_sending_day(today):
        print(f"{tag}WA — {today_str} non ouvrable (week-end ou jour férié) — pipeline annulé")
        return

    print(f"{tag}WA — mode={mode}")

    # ── CALL 1 : Relances prioritaires ──────────────────────────
    if mode in ("all", "relances"):
        relances = brevo.get_contacts(
            liste_id=source_list_id,
            brevo_filter=f'equals(PROPRIETAIRE,"{proprietaire}")' if proprietaire else None,
            attr_not_equals={"CATEGORIE": "Expert-comptable"},
            attr_lte={"PROCHAINE_RELANCE": today_str},
            attr_gte={"TOTAL_MESSAGE_ENVOYE": 1},
            debug=True,
        )
    else:
        relances = []

    print(f"{tag}WA — {len(relances)} relance(s) à traiter")

    # ── CALL 2 : Nouveaux contacts ───────────────────────────────
    nouveaux = []
    if mode in ("all", "nouveaux"):
        quota_nouveaux = max_par_jour - len(relances)
        if quota_nouveaux > 0:
            nouveaux = brevo.get_contacts(
                liste_id=source_list_id,
                brevo_filter=f'equals(PROPRIETAIRE,"{proprietaire}")' if proprietaire else None,
                attr_not_equals={"CATEGORIE": "Expert-comptable"},
                attr_lte={"TOTAL_MESSAGE_ENVOYE": 0},
                attr_is_null={"PROCHAINE_RELANCE": True, "WA_STATUS": True},
                debug=True,
            )

    print(f"{tag}WA — {len(nouveaux)} nouveau(x) à traiter")
    print(f"{tag}WA — Total : {len(relances) + len(nouveaux)} contact(s)")

    city_spacing_days: int = int(config.get("rules", {}).get("city_spacing_days", 30))
    one_month_ago = (today - timedelta(days=city_spacing_days)).isoformat()
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
    random.shuffle(nouveaux)
    if blocked_cities:
        print(f"{tag}WA — {len(blocked_cities)} ville(s) bloquée(s) (envoi < {city_spacing_days}j)")

    sent = 0
    dry_run_records: list = []

    # ── ÉTAPE 1 : RELANCES ──────────────────────────────────
    if mode in ("all", "relances"):
        for contact in relances:
            if sent >= max_par_jour:
                break

            attrs = contact.get("attributes", {})
            email: str = contact.get("email", "") or attrs.get("EMAIL", "") or ""
            sms: str = attrs.get("SMS", "") or ""
            company: str = attrs.get("COMPANY", "")
            wa_2: str = attrs.get("WA_2", "") or ""
            total_sent: int = int(attrs.get("TOTAL_MESSAGE_ENVOYE") or 0)

            if attrs.get("CATEGORIE") == "Expert-comptable":
                continue

            log_record: dict = {
                "config": config_name,
                "liste_id": source_list_id,
                "contact_email": email,
                "contact_company": company,
                "canal": "WHATSAPP",
            }

            try:
                if not wa_2:
                    brevo.move_to_list(email, source_list_id, 26)
                    print(f"{tag}  {company} ({email}) → WORKFLOW SMS (WA_2 vide)")
                    log_record.update(status="WORKFLOW SMS", erreur_detail="WA_2 vide")
                    log_entry(log_record)
                    increment_processed()
                    continue

                if dry_run:
                    print(f"{tag}  {company} ({email}) | RELANCE → DRY_RUN")
                    log_record.update(status="DRY_RUN", erreur_detail=f"[RELANCE] {wa_2[:200]}")
                    dry_run_records.append({
                        "type": "relance",
                        "email": email,
                        "company": company,
                        "wa_2": wa_2,
                    })
                    log_entry(log_record)
                    increment_processed()
                    sent += 1
                    time.sleep(1)
                    continue

                if not sms:
                    raise ValueError("Champ SMS vide — impossible d'envoyer WhatsApp")

                wasender.send_whatsapp(sms, wa_2)

                brevo.update_contact(
                    email,
                    {
                        "CANAL_PRINCIPAL": "WHATSAPP",
                        "TOTAL_MESSAGE_ENVOYE": total_sent + 1,
                        "DATE_DERNIERE_ENVOI": today_str,
                        "PROCHAINE_RELANCE": None,
                        "COMMENTAIRE": f"WA_2 envoyé le {today_str}",
                    },
                )

                print(f"  {company} ({email}) → RELANCE ENVOYÉE")
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

    # ── ÉTAPE 2 : NOUVEAUX ──────────────────────────────────
    if mode not in ("all", "nouveaux"):
        print(f"{tag}WA — terminé. {sent} message(s) envoyé(s).")
        return

    reste = max_par_jour - sent
    if not dry_run and reste <= 0:
        print(f"{tag}WA — limite atteinte après relances ({max_par_jour})")
        print(f"{tag}WA — terminé. {sent} message(s) envoyé(s).")
        return

    seen_cities_this_run: set = set()

    contacts_nouveaux = nouveaux if dry_run else nouveaux[:reste]
    for contact in contacts_nouveaux:
        if sent >= max_par_jour:
            break

        attrs = contact.get("attributes", {})
        email: str = contact.get("email", "") or attrs.get("EMAIL", "") or ""
        sms: str = attrs.get("SMS", "") or ""
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
            "canal": "WHATSAPP",
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

            has_whatsapp = wasender.check_whatsapp(sms) if sms else False
            log_record["whatsapp_check"] = "OUI" if has_whatsapp else "NON"

            if not has_whatsapp:
                print(f"{tag}  [AUDIT] {company} ({email}) | sms={sms!r} | has_whatsapp=False → bascule SMS")
                print(f"{tag}  [AUDIT] brevo.update_contact({email!r}, CANAL_PRINCIPAL=SMS) …")
                brevo.update_contact(
                    email,
                    {
                        "CANAL_PRINCIPAL": "SMS",
                        "COMMENTAIRE": f"Bascule SMS détectée le {today_str}",
                    },
                )
                print(f"{tag}  [AUDIT] update_contact OK → move_to_list({email!r}, {source_list_id} → 26) …")
                brevo.move_to_list(email, source_list_id, 26)
                print(f"{tag}  [AUDIT] move_to_list OK")
                print(f"{tag}  {company} ({email}) → WORKFLOW SMS (pas WhatsApp) → liste #26")
                log_record.update(status="WORKFLOW SMS", erreur_detail="Pas WhatsApp — bascule SMS")
                log_entry(log_record)
                increment_processed()
                continue

            is_facebook = bool(site_url and "facebook.com" in site_url.lower())

            if is_facebook:
                pagespeed_score = None
            else:
                ps_result = pagespeed.analyse(site_url) if site_url else {}
                pagespeed_score = ps_result.get("score") if ps_result else None
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
                )
                if angle is None:
                    reason = angle_detector.explain_skip(
                        contact, pagespeed_score,
                        reputation_candidates=rep_candidates,
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

            if angle != "INVISIBILITÉ" and not concurrent_data["concurrent_1"]["nom"]:
                brevo.move_to_list(email, source_list_id, 27)
                reason = f"Aucun concurrent trouvé pour l'angle {angle}"
                print(f"{tag}  {company} ({email}) → HORS SCOPE | {reason}")
                log_record.update(status="HORS SCOPE", erreur_detail=reason)
                log_entry(log_record)
                increment_processed()
                continue

            log_record["concurrent_1"] = concurrent_data["concurrent_1"]["nom"]
            log_record["concurrent_2"] = concurrent_data["concurrent_2"]["nom"]

            firstname = attrs.get("FIRSTNAME", "") or ""
            lastname = attrs.get("LASTNAME", "") or ""
            proprietaire_val = attrs.get("PROPRIETAIRE", "") or ""
            c1 = concurrent_data["concurrent_1"]
            c2 = concurrent_data["concurrent_2"]
            print(f"{tag}  ── Contact ──────────────────────────────────────────")
            print(f"{tag}  {firstname} {lastname} / {company}")
            print(f"{tag}  Niche        : {niche}")
            print(f"{tag}  Ville        : {concurrent_data.get('ville_nom', city_id)}")
            print(f"{tag}  Propriétaire : {proprietaire_val}")
            print(f"{tag}  Concurrent 1 : {c1['nom']} | {c1['note']} ⭐ | {c1['nb_avis']} avis")
            print(f"{tag}  Concurrent 2 : {c2['nom']} | {c2['note']} ⭐ | {c2['nb_avis']} avis")
            print(f"{tag}  ─────────────────────────────────────────────────────")

            messages = message_builder.build_all(
                contact=contact,
                angle=angle,
                concurrent_data=concurrent_data,
                combos_filter=["wa_1", "wa_2"],
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

            wa_1: str = messages["wa_1"]["corps"]
            wa_2: str = messages["wa_2"]["corps"]

            print(f"{tag}  ── Messages générés ──────────────────────────────────")
            print(f"{tag}  Message 1 (WA_1) : {wa_1[:300]}")
            print(f"{tag}  Message 2 (WA_2) : {wa_2[:300]}")
            print(f"{tag}  ─────────────────────────────────────────────────────")

            if test_phone:
                print(f"{tag}  {company} ({email}) | {angle} → TEST SEND → {test_phone}")
                wasender.send_whatsapp(test_phone, wa_1)
                print(f"{tag}  [TEST] WA_1 envoyé à {test_phone} ✓")
                log_record.update(status="TEST", erreur_detail=f"[TEST → {test_phone}] {wa_1[:200]}")
                log_entry(log_record)
                increment_processed()
                sent += 1
                break  # un seul envoi test

            if dry_run:
                print(f"{tag}  {company} ({email}) | {angle} → DRY_RUN")
                log_record.update(status="DRY_RUN", erreur_detail=f"[WA_1] {wa_1[:200]}")
                dry_run_records.append({
                    "type": "nouveau",
                    "email": email,
                    "prenom": attrs.get("FIRSTNAME", "") or attrs.get("PRENOM", ""),
                    "company": company,
                    "niche": niche,
                    "ville": concurrent_data.get("ville_nom", city_id),
                    "proprietaire": attrs.get("PROPRIETAIRE", ""),
                    "angle": angle,
                    "concurrent_1": {"nom": c1["nom"], "note": c1["note"], "nb_avis": c1["nb_avis"]},
                    "concurrent_2": {"nom": c2["nom"], "note": c2["note"], "nb_avis": c2["nb_avis"]},
                    "wa_1": wa_1,
                    "wa_2": wa_2,
                })
                log_entry(log_record)
                increment_processed()
                sent += 1
                time.sleep(1)
                continue

            if not sms:
                raise ValueError("Champ SMS vide — impossible d'envoyer WhatsApp")

            wasender.send_whatsapp(sms, wa_1)

            prochaine_relance = (today + timedelta(days=3)).isoformat()

            brevo.update_contact(
                email,
                {
                    "ANGLE": angle,
                    "CANAL_PRINCIPAL": "WHATSAPP",
                    "WA_1": wa_1,
                    "WA_2": wa_2,
                    "TOTAL_MESSAGE_ENVOYE": 1,
                    "DATE_DERNIERE_ENVOI": today_str,
                    "PROCHAINE_RELANCE": prochaine_relance,
                    "COMMENTAIRE": f"WA_1 envoyé le {today_str} | Angle: {angle}",
                },
            )

            print(f"  {company} ({email}) → ENVOYÉ | {angle}")
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
        output_path = Path(f"output/dry_run_wa_{config_name}_{today_str}.yaml")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml.dump(dry_run_records, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"{tag}WA — résultats écrits dans {output_path}")

    print(f"{tag}WA — terminé. {sent} message(s) envoyé(s).")
