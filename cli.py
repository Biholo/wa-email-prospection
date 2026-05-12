"""
CLI for manually running pipelines without the cron scheduler.

Usage:
  python cli.py develly                              # run both email + whatsapp
  python cli.py develly --dry-run                    # simulate without touching Brevo
  python cli.py develly --only email                 # email pipeline only
  python cli.py develly --only whatsapp              # WhatsApp pipeline only
  python cli.py develly --test-phone 0641552699      # dry-run + envoie wa_1 à ce numéro
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from core.db import init_db  # noqa: E402 (after load_dotenv)


def _build_services(config_name: str, config: dict) -> dict:
    from logic.angle_detector import AngleDetector
    from logic.competitor_resolver import CompetitorResolver
    from logic.message_builder import MessageBuilder
    from services.brevo_service import BrevoService
    from services.wasender_service import WasenderService
    from services.pagespeed_service import PageSpeedService
    from services.supabase_service import SupabaseService

    supabase = SupabaseService()
    return {
        "brevo":               BrevoService(),
        "wasender":            WasenderService(),
        "pagespeed":           PageSpeedService(),
        "angle_detector":      AngleDetector(config),
        "competitor_resolver": CompetitorResolver(supabase),
        "message_builder":     MessageBuilder(config_name, config),
    }


def _normalize_phone(raw: str) -> str:
    """Converts French mobile (06/07) to international format (336/337)."""
    import re
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if digits.startswith("0") and len(digits) == 10:
        digits = "33" + digits[1:]
    return digits


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Develly B2B — manual pipeline runner"
    )
    parser.add_argument(
        "config",
        help="Config filename without extension (e.g. develly)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate messages and log, do not write to Brevo",
    )
    parser.add_argument(
        "--only",
        choices=["email", "whatsapp"],
        default=None,
        help="Run only one pipeline (default: both)",
    )
    parser.add_argument(
        "--test-phone",
        default="",
        metavar="NUMERO",
        help="Dry-run + envoie wa_1 à ce numéro (ex: 0641552690 ou 33641552690)",
    )
    args = parser.parse_args()

    config_path = Path(f"config/{args.config}.yaml")
    if not config_path.exists():
        print(f"Error: config '{args.config}' not found ({config_path})")
        sys.exit(1)

    import yaml

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    init_db()

    test_phone = _normalize_phone(args.test_phone)
    if test_phone:
        args.dry_run = True
        args.only = "whatsapp"

    mode = "[DRY RUN] " if args.dry_run else ""
    target = args.only or "email + whatsapp"
    print(f"{mode}Pipeline {target} — config={args.config}")
    if test_phone:
        print(f"  [TEST] Envoi WA_1 au numéro : {test_phone}")

    svc = _build_services(args.config, config)

    shared = {
        "config_name":         args.config,
        "config":              config,
        "angle_detector":      svc["angle_detector"],
        "competitor_resolver": svc["competitor_resolver"],
        "message_builder":     svc["message_builder"],
        "dry_run":             args.dry_run,
    }

    run_email = args.only in (None, "email")
    run_wa = args.only in (None, "whatsapp")

    if run_email:
        from scheduler.pipeline_email import run_email_pipeline
        run_email_pipeline(**shared, brevo=svc["brevo"], pagespeed=svc["pagespeed"])

    if run_wa:
        from scheduler.pipeline_whatsapp import run_whatsapp_pipeline
        run_whatsapp_pipeline(
            **shared,
            brevo=svc["brevo"],
            wasender=svc["wasender"],
            pagespeed=svc["pagespeed"],
            test_phone=test_phone,
        )

    print("Done.")


if __name__ == "__main__":
    main()
