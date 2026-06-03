import random
from datetime import date

from core.db import log_entry
from core.holidays import is_sending_day
from core.state import increment_errors, increment_processed
from core.mailer import RecapTracker
from services.brevo_service import BrevoService

SOURCE_LIST_ID = 20
TARGET_LIST_ID = 30
MAX_PAR_JOUR = 20


def run_apporteur_pipeline(
    brevo: BrevoService,
    dry_run: bool = False,
) -> None:
    tag = "[DRY RUN] " if dry_run else ""
    today_date = date.today()
    tracker = RecapTracker("APPORTEUR", "develly", dry_run)

    if not is_sending_day(today_date):
        print(f"{tag}APPORTEUR — {today_date.isoformat()} non ouvrable (week-end ou jour férié) — pipeline annulé")
        return

    contacts = brevo.get_contacts(
        liste_id=SOURCE_LIST_ID,
        attr_equals={"CATEGORIE": "Expert-comptable"},
    )

    print(f"{tag}APPORTEUR — {len(contacts)} Expert-comptable(s) dans liste #{SOURCE_LIST_ID}")

    random.shuffle(contacts)
    moved = 0

    for contact in contacts:
        if moved >= MAX_PAR_JOUR:
            print(f"{tag}APPORTEUR — limite journalière atteinte ({MAX_PAR_JOUR})")
            break

        attrs = contact.get("attributes", {})
        email: str = contact.get("email", "") or attrs.get("EMAIL", "") or ""
        company: str = attrs.get("COMPANY", "")

        log_record: dict = {
            "config": "apporteur",
            "liste_id": SOURCE_LIST_ID,
            "contact_email": email,
            "contact_company": company,
            "canal": "APPORTEUR",
        }

        try:
            if dry_run:
                print(f"{tag}  {company} ({email}) → DRY_RUN liste #{SOURCE_LIST_ID} → #{TARGET_LIST_ID}")
                log_record.update(status="DRY_RUN", erreur_detail=f"Déplacement vers liste #{TARGET_LIST_ID}")
                log_entry(log_record)
                increment_processed()
                moved += 1
                tracker.record("DRY_RUN", f"{company} ({email})")
                continue

            brevo.move_to_list(email, SOURCE_LIST_ID, TARGET_LIST_ID)

            print(f"  {company} ({email}) → OK liste #{SOURCE_LIST_ID} → #{TARGET_LIST_ID}")
            log_record["status"] = "DÉPLACÉ"
            log_entry(log_record)
            increment_processed()
            moved += 1
            tracker.record("DÉPLACÉ", f"{company} ({email})")

        except Exception as exc:
            print(f"{tag}  {company} ({email}) → ERREUR : {exc}")
            log_record.update(status="ERREUR", erreur_detail=str(exc))
            log_entry(log_record)
            increment_errors()
            increment_processed()
            tracker.record("ERREUR")

    print(f"{tag}APPORTEUR — terminé. {moved} déplacé(s).")
    tracker.send(len(contacts))
