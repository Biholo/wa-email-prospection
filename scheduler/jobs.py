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


def init_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Europe/Paris")

    for config_path in sorted(Path("config").glob("*.yaml")):
        config_name = config_path.stem
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        sched_cfg = config.get("scheduler", {})
        tz: str = sched_cfg.get("timezone", "Europe/Paris")

        # ── Email pipeline ────────────────────────────────────────────
        cron_email: str = sched_cfg.get("cron", "0 9 * * 1-5")
        scheduler.add_job(
            run_email_only,
            CronTrigger.from_crontab(cron_email, timezone=tz),
            args=[config_name],
            id=f"email_{config_name}",
            replace_existing=True,
        )

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

    # --- Ajouter d'autres jobs ici ---
    # from scheduler.reporting import run_daily_report
    # scheduler.add_job(run_daily_report, CronTrigger.from_crontab("0 8 * * *"), id="daily_report")

    scheduler.start()
    return scheduler
