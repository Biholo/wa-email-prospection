import random
import time
from abc import ABC, abstractmethod
from datetime import date, timedelta
from pathlib import Path

from core.db import log_entry
from core.holidays import is_sending_day, add_business_days
from core.mailer import RecapTracker
from core.state import increment_errors, increment_processed
from logic.angle_detector import AngleDetector
from logic.competitor_resolver import CompetitorResolver, _normalize_niche
from services.audit_service import AuditService, ANGLE_TO_AUDIT_TYPE
from services.brevo_service import BrevoService
from services.pagespeed_service import PageSpeedService

# Niches jamais à contacter — filtre de sécurité en-loop
BLOCKED_NICHES: frozenset[str] = frozenset({"comptable", "cgp", "iad", "agent_immo"})


class ChannelAdapter(ABC):
    """Override pour brancher un nouveau canal sur run_pipeline."""

    canal: str  # "EMAIL" | "WHATSAPP" — à définir dans la sous-classe

    @abstractmethod
    def send_relance(self, contact: dict, attrs: dict) -> None:
        """Envoie le message de relance. Lève une exception en cas d'échec."""
        ...

    @abstractmethod
    def send_nouveau(
        self,
        contact: dict,
        attrs: dict,
        pdf_url: str,
        pdf_bytes: bytes,
        concurrent_data: dict,
        angle: str,
    ) -> None:
        """Envoie l'audit premier contact. Lève une exception en cas d'échec."""
        ...

    def after_relance_sent(self, contact: dict, attrs: dict) -> None:
        """Hook post-envoi relance (ex: save_wa_message)."""
        pass

    def after_nouveau_sent(self, contact: dict, attrs: dict) -> None:
        """Hook post-envoi nouveau (ex: save_wa_message)."""
        pass

    def pre_nouveau_check(
        self,
        contact: dict,
        attrs: dict,
        brevo: BrevoService,
        source_list_id: int,
        today_str: str,
        log_record: dict,
        tag: str,
    ) -> bool:
        """Gate canal-spécifique avant traitement d'un nouveau.
        Retourne False pour skipper (doit setter log_record status/erreur_detail avant)."""
        return True

    def nouveaux_extra_null_filter(self) -> dict:
        """Filtres attr_is_null additionnels pour le fetch nouveaux (ex: WA_STATUS)."""
        return {}

    @property
    def is_test_send(self) -> bool:
        """Quand True : envoi réel mais Brevo non mis à jour, loggé TEST, stop après 1 contact."""
        return False

    def relance_brevo_attrs(self, total_sent: int, today_str: str) -> dict:
        return {
            "CANAL_PRINCIPAL": self.canal,
            "TOTAL_MESSAGE_ENVOYE": total_sent + 1,
            "DATE_DERNIERE_ENVOI": today_str,
            "PROCHAINE_RELANCE": None,
            "COMMENTAIRE": f"Relance {self.canal} envoyée le {today_str}",
        }

    def nouveau_brevo_attrs(self, angle: str, pdf_url: str, today_str: str, prochaine_relance: str) -> dict:
        return {
            "ANGLE": angle,
            "CANAL_PRINCIPAL": self.canal,
            "PDF_URL": pdf_url,
            "TOTAL_MESSAGE_ENVOYE": 1,
            "DATE_DERNIERE_ENVOI": today_str,
            "PROCHAINE_RELANCE": prochaine_relance,
            "COMMENTAIRE": f"Audit {self.canal} envoyé le {today_str} | Angle: {angle}",
        }


def run_pipeline(
    adapter: ChannelAdapter,
    config_name: str,
    config: dict,
    brevo: BrevoService,
    pagespeed: PageSpeedService,
    angle_detector: AngleDetector,
    competitor_resolver: CompetitorResolver,
    audit_service: AuditService,
    dry_run: bool = False,
    mode: str = "all",
    source_list_id_override: int | None = None,
    tag_override: str = "",
) -> None:
    canal = adapter.canal
    workflow = config.get("workflow", {}).get(canal.lower(), {})
    source_list_id: int = source_list_id_override or int(workflow["source_list_id"])
    max_par_jour: int = int(workflow.get("max_per_day", 200))
    delai: int = int(config.get("rules", {}).get("send_delay", 30))
    proprietaire: str = config.get("company", {}).get("owner", "")

    tag = tag_override if tag_override else ("[DRY RUN] " if dry_run else "")
    tracker = RecapTracker(canal, config_name, dry_run)

    today = date.today()
    today_str = today.isoformat()

    if not dry_run and not is_sending_day(today):
        print(f"{tag}{canal} — {today_str} non ouvrable (week-end ou jour férié) — pipeline annulé")
        return

    brevo_filter = f'equals(PROPRIETAIRE,"{proprietaire}")' if proprietaire else None

    # ── Relances ──────────────────────────────────────────────────
    relances = []
    if mode in ("all", "relances"):
        relances = brevo.get_contacts(
            liste_id=source_list_id,
            brevo_filter=brevo_filter,
            attr_not_equals={"CATEGORIE": "Expert-comptable"},
            attr_lte={"PROCHAINE_RELANCE": today_str},
            attr_gte={"TOTAL_MESSAGE_ENVOYE": 1},
            debug=True,
        )
    print(f"{tag}{canal} — {len(relances)} relance(s) à traiter")

    # ── Nouveaux ──────────────────────────────────────────────────
    nouveaux = []
    if mode in ("all", "nouveaux"):
        nouveaux = brevo.get_contacts(
            liste_id=source_list_id,
            brevo_filter=brevo_filter,
            attr_not_equals={"CATEGORIE": "Expert-comptable"},
            attr_lte={"TOTAL_MESSAGE_ENVOYE": 0},
            attr_is_null={"PROCHAINE_RELANCE": True, **adapter.nouveaux_extra_null_filter()},
            debug=True,
        )
    print(f"{tag}{canal} — {len(nouveaux)} nouveau(x) à traiter")

    # ── Villes bloquées ───────────────────────────────────────────
    city_spacing_days: int = int(config.get("rules", {}).get("city_spacing_days", 30))
    one_month_ago = (today - timedelta(days=city_spacing_days)).isoformat()
    recently_sent = brevo.get_contacts(
        liste_id=source_list_id,
        brevo_filter=brevo_filter,
        attr_gte={"TOTAL_MESSAGE_ENVOYE": 1, "DATE_DERNIERE_ENVOI": one_month_ago},
    )
    blocked_cities: set = {
        c.get("attributes", {}).get("CITY_ID", "")
        for c in recently_sent
        if c.get("attributes", {}).get("CITY_ID")
    }
    if blocked_cities:
        print(f"{tag}{canal} — {len(blocked_cities)} ville(s) bloquée(s) (envoi < {city_spacing_days}j)")
    random.shuffle(nouveaux)

    sent = 0

    # ── Boucle relances ───────────────────────────────────────────
    if mode in ("all", "relances"):
        for contact in relances:
            if sent >= max_par_jour:
                break

            attrs = contact.get("attributes", {})
            contact_id = contact.get("id")
            email: str = contact.get("email", "") or attrs.get("EMAIL", "") or ""
            company: str = attrs.get("COMPANY", "")
            total_sent: int = int(attrs.get("TOTAL_MESSAGE_ENVOYE") or 0)

            log_record: dict = {
                "config": config_name,
                "liste_id": source_list_id,
                "contact_email": email,
                "contact_company": company,
                "canal": canal,
            }

            try:
                if dry_run:
                    print(f"{tag}  {company} ({email}) | RELANCE → DRY_RUN")
                    log_record.update(status="DRY_RUN", erreur_detail=f"[RELANCE {canal}]")
                    log_entry(log_record)
                    increment_processed()
                    sent += 1
                    tracker.record("DRY_RUN", f"{company} ({email}) | RELANCE")
                    time.sleep(1)
                    continue

                adapter.send_relance(contact, attrs)
                adapter.after_relance_sent(contact, attrs)
                brevo.update_contact(contact_id, adapter.relance_brevo_attrs(total_sent, today_str))

                print(f"  {company} ({email}) → RELANCE ENVOYÉE")
                log_record["status"] = "ENVOYÉ"
                log_entry(log_record)
                increment_processed()
                sent += 1
                tracker.record("ENVOYÉ", f"{company} ({email}) | RELANCE")
                time.sleep(delai)

            except Exception as exc:
                print(f"{tag}  {company} ({email}) → ERREUR relance : {exc}")
                log_record.update(status="ERREUR", erreur_detail=str(exc))
                log_entry(log_record)
                increment_errors()
                increment_processed()
                tracker.record("ERREUR")

    # ── Boucle nouveaux ───────────────────────────────────────────
    if mode not in ("all", "nouveaux"):
        print(f"{tag}{canal} — terminé. {sent} traité(s).")
        tracker.send(len(relances) + len(nouveaux))
        return

    reste = max_par_jour - sent
    if not dry_run and reste <= 0:
        print(f"{tag}{canal} — limite atteinte après relances ({max_par_jour})")
        print(f"{tag}{canal} — terminé. {sent} traité(s).")
        tracker.send(len(relances) + len(nouveaux))
        return

    seen_cities_this_run: set = set()
    contacts_nouveaux = nouveaux if dry_run else nouveaux[:reste]

    for contact in contacts_nouveaux:
        if not dry_run and sent >= max_par_jour:
            break

        attrs = contact.get("attributes", {})
        contact_id = contact.get("id")
        email: str = contact.get("email", "") or attrs.get("EMAIL", "") or ""
        company: str = attrs.get("COMPANY", "")
        city_id: str = attrs.get("CITY_ID", "")
        niche: str = attrs.get("CATEGORIE", "")
        site_url: str = attrs.get("WEBSITE_URL", "") or ""

        if not niche or niche == "Non catégorisé":
            print(f"{tag}  {company} ({email}) → SKIPPÉ (niche non catégorisée)")
            continue

        if _normalize_niche(niche) in BLOCKED_NICHES:
            continue

        log_record: dict = {
            "config": config_name,
            "liste_id": source_list_id,
            "contact_email": email,
            "contact_company": company,
            "canal": canal,
        }

        try:
            if city_id and (city_id in blocked_cities or city_id in seen_cities_this_run):
                print(f"{tag}  {company} ({email}) → SKIPPÉ (ville déjà envoyée récemment)")
                log_record.update(status="SKIPPÉ", erreur_detail=f"Ville {city_id} déjà contactée")
                log_entry(log_record)
                increment_processed()
                continue

            if city_id:
                seen_cities_this_run.add(city_id)

            if not adapter.pre_nouveau_check(
                contact, attrs, brevo, source_list_id, today_str, log_record, tag
            ):
                log_entry(log_record)
                increment_processed()
                continue

            is_facebook = bool(site_url and "facebook.com" in site_url.lower())
            pagespeed_score = None
            if not is_facebook:
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

            if is_facebook:
                angle = "INVISIBILITÉ"
            else:
                angle = angle_detector.detect(contact, pagespeed_score, reputation_candidates=rep_candidates)
                if angle is None:
                    reason = angle_detector.explain_skip(
                        contact, pagespeed_score, reputation_candidates=rep_candidates
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
                lead_note=lead_note,
                lead_avis=int(attrs.get("NUMBER_OF_RATE") or 0),
            )

            if angle == "RÉPUTATION" and not concurrent_data["concurrent_1"]["nom"]:
                brevo.move_to_list(email, source_list_id, 27)
                reason = f"Aucun concurrent trouvé pour l'angle {angle}"
                print(f"{tag}  {company} ({email}) → HORS SCOPE | {reason}")
                log_record.update(status="HORS SCOPE", erreur_detail=reason)
                log_entry(log_record)
                increment_processed()
                continue

            c1 = concurrent_data["concurrent_1"]
            c2 = concurrent_data["concurrent_2"]
            log_record["concurrent_1"] = c1.get("nom")
            log_record["concurrent_2"] = c2.get("nom")

            niche_key = _normalize_niche(niche)
            niche_config = audit_service.load_niche_config(niche_key)
            if not niche_config:
                print(f"{tag}  {company} ({email}) → SKIPPÉ (config niche manquante : {niche_key!r})")
                log_record.update(status="SKIPPÉ", erreur_detail=f"Niche config manquante : {niche_key!r}")
                log_entry(log_record)
                increment_processed()
                continue

            audit_type = ANGLE_TO_AUDIT_TYPE.get(angle)
            if not audit_type:
                print(f"{tag}  {company} ({email}) → SKIPPÉ (angle inconnu : {angle!r})")
                log_record.update(status="SKIPPÉ", erreur_detail=f"Angle inconnu : {angle!r}")
                log_entry(log_record)
                increment_processed()
                continue

            lead_data = competitor_resolver.get_lead_data(email)
            print(f"{tag}  ── {company} ──")
            print(f"{tag}  Niche={niche} | Ville={concurrent_data.get('ville_nom', city_id)} | Angle={angle}")
            print(f"{tag}  C1={c1['nom']} {c1['note']}⭐ | C2={c2['nom']} {c2['note']}⭐")

            if dry_run:
                # Render PDF uniquement ici — évite double render en prod
                variables = audit_service.build_variables(contact, concurrent_data, niche_config, lead_data)
                pdf_bytes = audit_service.render_slides_to_pdf(audit_type, variables)
                out_dir = Path("output")
                out_dir.mkdir(parents=True, exist_ok=True)
                safe = company.lower().replace(" ", "_")
                pdf_path = out_dir / f"dry_run_{canal.lower()}_{config_name}_{today_str}_{safe}.pdf"
                pdf_path.write_bytes(pdf_bytes)
                print(f"{tag}  {company} ({email}) | {angle} → DRY_RUN")
                print(f"{tag}  PDF → {pdf_path}")
                log_record.update(
                    status="DRY_RUN",
                    erreur_detail=f"[AUDIT {canal}] angle={angle} niche={niche_key} pdf={pdf_path}",
                )
                log_entry(log_record)
                increment_processed()
                sent += 1
                tracker.record(
                    "DRY_RUN",
                    f"{company} | {niche} | {concurrent_data.get('ville_nom', city_id)} | {angle}",
                )
                time.sleep(1)
                continue

            # Chemin réel — un seul render + upload
            pdf_url, pdf_bytes = audit_service.generate_and_upload(
                contact, concurrent_data, niche_config, audit_type, lead_data
            )

            adapter.send_nouveau(contact, attrs, pdf_url, pdf_bytes, concurrent_data, angle)
            adapter.after_nouveau_sent(contact, attrs)

            if adapter.is_test_send:
                print(f"  [TEST] {company} ({email}) → AUDIT WA envoyé au numéro test | {angle}")
                log_record["status"] = "TEST"
                log_entry(log_record)
                increment_processed()
                sent += 1
                tracker.record(
                    "TEST",
                    f"{company} | {niche} | {concurrent_data.get('ville_nom', city_id)} | {angle}",
                )
                break  # 1 seul envoi en mode test

            prochaine_relance = add_business_days(today, 4).isoformat()
            brevo.update_contact(
                contact_id,
                adapter.nouveau_brevo_attrs(angle, pdf_url, today_str, prochaine_relance),
            )

            print(f"  {company} ({email}) → AUDIT ENVOYÉ | {angle}")
            log_record["status"] = "ENVOYÉ"
            log_entry(log_record)
            increment_processed()
            sent += 1
            tracker.record(
                "ENVOYÉ",
                f"{company} | {niche} | {concurrent_data.get('ville_nom', city_id)} | {angle}",
            )
            time.sleep(delai)

        except Exception as exc:
            print(f"{tag}  {company} ({email}) → ERREUR : {exc}")
            log_record.update(status="ERREUR", erreur_detail=str(exc))
            log_entry(log_record)
            increment_errors()
            increment_processed()
            tracker.record("ERREUR")

    print(f"{tag}{canal} — terminé. {sent} traité(s).")
    tracker.send(len(relances) + len(nouveaux))
