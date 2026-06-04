"""
Registre des jobs APScheduler.

Ajouter un nouveau job :
  1. Créer scheduler/<nom_du_job>.py avec une fonction run_<nom>()
  2. L'importer ici et l'enregistrer avec scheduler.add_job()
"""
from pathlib import Path

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.pipeline import run_email_only, run_wa_only
from scheduler.pipeline_apporteur import run_apporteur_pipeline
from scheduler.recap import run_daily_recap
from services.brevo_service import BrevoService


def init_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Europe/Paris")

    for config_path in sorted(Path("config").glob("*.yaml")):
        config_name = config_path.stem
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        sched_cfg = config.get("scheduler", {})
        tz: str = sched_cfg.get("timezone", "Europe/Paris")

        # ── Email matin — 10h00 lun-ven ──────────────────────────────
        cron_email1: str = sched_cfg.get("cron_email1", "0 10 * * 1-5")
        scheduler.add_job(
            run_email_only,
            CronTrigger.from_crontab(cron_email1, timezone=tz),
            args=[config_name],
            id=f"email1_{config_name}",
            replace_existing=True,
        )

        # ── WA nouveaux — 11h00 lun-ven ──────────────────────────────
        cron_wa1: str = sched_cfg.get("cron_wa1", "0 11 * * 1-5")
        scheduler.add_job(
            run_wa_only,
            CronTrigger.from_crontab(cron_wa1, timezone=tz),
            args=[config_name, "nouveaux"],
            id=f"wa1_{config_name}",
            replace_existing=True,
        )

        # ── Email après-midi — 14h00 lun-ven ─────────────────────────
        cron_email2: str = sched_cfg.get("cron_email2", "0 14 * * 1-5")
        scheduler.add_job(
            run_email_only,
            CronTrigger.from_crontab(cron_email2, timezone=tz),
            args=[config_name],
            id=f"email2_{config_name}",
            replace_existing=True,
        )

        # ── WA tous (nouveaux + relances) — 17h30 lun-ven ────────────
        cron_wa2: str = sched_cfg.get("cron_wa2", "30 17 * * 1-5")
        scheduler.add_job(
            run_wa_only,
            CronTrigger.from_crontab(cron_wa2, timezone=tz),
            args=[config_name, "all"],
            id=f"wa2_{config_name}",
            replace_existing=True,
        )

    # ── Apporteur d'affaire — 9h00 et 13h00 lun-ven ─────────────────────
    brevo_apporteur = BrevoService()
    scheduler.add_job(
        run_apporteur_pipeline,
        CronTrigger.from_crontab("0 9 * * 1-5", timezone="Europe/Paris"),
        kwargs={"brevo": brevo_apporteur},
        id="apporteur_matin",
        replace_existing=True,
    )
    scheduler.add_job(
        run_apporteur_pipeline,
        CronTrigger.from_crontab("0 13 * * 1-5", timezone="Europe/Paris"),
        kwargs={"brevo": brevo_apporteur},
        id="apporteur_midi",
        replace_existing=True,
    )

    # ── Récap quotidien scraping — 13h00 tous les jours ──────────────────
    scheduler.add_job(
        run_daily_recap,
        CronTrigger.from_crontab("0 13 * * *", timezone="Europe/Paris"),
        id="recap_wa",
        replace_existing=True,
    )

    scheduler.start()
    return scheduler
