"""
Orchestrateur principal.
Lance les pipelines email et WhatsApp en parallèle (threads).
"""
import threading
from datetime import datetime
from pathlib import Path

import yaml

from core.state import get_state, update_state
from logic.angle_detector import AngleDetector
from logic.competitor_resolver import CompetitorResolver
from services.audit_service import AuditService
from services.brevo_service import BrevoService
from services.pagespeed_service import PageSpeedService
from services.supabase_service import SupabaseService


def _load_config(config_name: str) -> dict | None:
    config_path = Path(f"config/{config_name}.yaml")
    if not config_path.exists():
        return None
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def _acquire(config_name: str) -> bool:
    if get_state()["running"]:
        return False
    update_state(
        running=True,
        processed=0,
        errors=0,
        config=config_name,
        liste_id=None,
        started_at=datetime.now().isoformat(),
    )
    return True


def _build_shared(config_name: str, config: dict, dry_run: bool) -> dict:
    supabase = SupabaseService()
    return dict(
        config_name=config_name,
        config=config,
        angle_detector=AngleDetector(config),
        competitor_resolver=CompetitorResolver(supabase),
        audit_service=AuditService(supabase),
        dry_run=dry_run,
    )


def run_email_only(config_name: str, dry_run: bool = False) -> None:
    from scheduler.pipeline_email import run_email_pipeline
    from services.resend_service import ResendService
    config = _load_config(config_name)
    if config is None or not _acquire(config_name):
        return
    shared = _build_shared(config_name, config, dry_run)
    try:
        run_email_pipeline(**shared, brevo=BrevoService(), resend=ResendService(), pagespeed=PageSpeedService())
    finally:
        update_state(running=False)


def run_wa_only(
    config_name: str,
    mode: str = "all",
    dry_run: bool = False,
    test_mode: bool = False,
) -> None:
    from scheduler.pipeline_whatsapp import run_whatsapp_pipeline
    from services.wasender_service import WasenderService
    config = _load_config(config_name)
    if config is None or not _acquire(config_name):
        return
    shared = _build_shared(config_name, config, dry_run)
    try:
        run_whatsapp_pipeline(
            **shared,
            brevo=BrevoService(),
            wasender=WasenderService(),
            pagespeed=PageSpeedService(),
            mode=mode,
            test_mode=test_mode,
        )
    finally:
        update_state(running=False)


def run_prospecting(config_name: str, dry_run: bool = False) -> None:
    """Lance email + WA en parallèle (déclenchement manuel / API)."""
    from scheduler.pipeline_email import run_email_pipeline
    from scheduler.pipeline_whatsapp import run_whatsapp_pipeline
    from services.resend_service import ResendService
    from services.wasender_service import WasenderService

    config = _load_config(config_name)
    if config is None or not _acquire(config_name):
        return

    shared = _build_shared(config_name, config, dry_run)
    brevo = BrevoService()
    pagespeed = PageSpeedService()

    email_thread = threading.Thread(
        target=run_email_pipeline,
        kwargs={**shared, "brevo": brevo, "resend": ResendService(), "pagespeed": pagespeed},
        daemon=True,
        name=f"email-{config_name}",
    )
    whatsapp_thread = threading.Thread(
        target=run_whatsapp_pipeline,
        kwargs={**shared, "brevo": brevo, "wasender": WasenderService(), "pagespeed": pagespeed},
        daemon=True,
        name=f"whatsapp-{config_name}",
    )

    try:
        email_thread.start()
        whatsapp_thread.start()
        email_thread.join()
        whatsapp_thread.join()
    finally:
        update_state(running=False)
