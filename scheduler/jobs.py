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

from scheduler.pipeline import run_wa_only
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

        # ── WA nouveaux (wa_1) — 12h00 lun-ven ───────────────────────
        cron_wa1: str = sched_cfg.get("cron_wa1", "0 12 * * 1-5")
        scheduler.add_job(
            run_wa_only,
            CronTrigger.from_crontab(cron_wa1, timezone=tz),
            args=[config_name, "nouveaux"],
            id=f"wa1_{config_name}",
            replace_existing=True,
        )

        # ── WA relances (wa_2) — 17h00 lun-ven ───────────────────────
        cron_wa2: str = sched_cfg.get("cron_wa2", "0 17 * * 1-5")
        scheduler.add_job(
            run_wa_only,
            CronTrigger.from_crontab(cron_wa2, timezone=tz),
            args=[config_name, "relances"],
            id=f"wa2_{config_name}",
            replace_existing=True,
        )

    # ── Apporteur d'affaire — 9h00 lun-ven ──────────────────────────────
    scheduler.add_job(
        run_apporteur_pipeline,
        CronTrigger.from_crontab("0 9 * * 1-5", timezone="Europe/Paris"),
        kwargs={"brevo": BrevoService()},
        id="apporteur",
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
