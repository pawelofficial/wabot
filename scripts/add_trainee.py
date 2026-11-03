"""Register a trainee (and their trainer), or update one – until the dashboard does this.

The bot answers only registered numbers; anyone else gets a fixed "not registered" reply. With
no options this registers your own number (WHATSAPP_TEST_RECIPIENT) with the example plan.
Running it again for the same number changes only the options you pass.

Run from the project root, after `uv run alembic upgrade head`:
    uv run python scripts/add_trainee.py
    uv run python scripts/add_trainee.py --name "Ania" --plan plans/example.txt
    uv run python scripts/add_trainee.py --phone 48123456789 --trainer "Trener Tomek"
    uv run python scripts/add_trainee.py --no-plan
"""

import argparse
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import OperationalError

from wabot import trainees
from wabot.config import get_settings
from wabot.db import SessionLocal
from wabot.models import DEFAULT_TIMEZONE, Trainee

DEFAULT_TRAINER = "Trener testowy"
DEFAULT_NAME = "Podopieczny testowy"
DEFAULT_PLAN = Path("plans/example.txt")


def main(argv: list[str] | None = None) -> None:
    # Windows consoles default to cp1252, which can't print Polish letters.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--phone", help="digits with country code (default: WHATSAPP_TEST_RECIPIENT)"
    )
    parser.add_argument("--name", help=f"trainee's name (new trainee: {DEFAULT_NAME!r})")
    parser.add_argument("--trainer", help=f"trainer's name, created if new ({DEFAULT_TRAINER!r})")
    plan = parser.add_mutually_exclusive_group()
    plan.add_argument("--plan", type=Path, help=f"training plan text file ({DEFAULT_PLAN})")
    plan.add_argument("--no-plan", action="store_true", help="remove the training plan")
    parser.add_argument(
        "--consent-date",
        type=dt.date.fromisoformat,
        help="day the paper consent was signed, YYYY-MM-DD (new trainee: today)",
    )
    parser.add_argument("--timezone", help=f"IANA timezone (new trainee: {DEFAULT_TIMEZONE})")
    args = parser.parse_args(argv)

    phone = args.phone or get_settings().whatsapp_test_recipient
    if not phone:
        sys.exit("No number: pass --phone or set WHATSAPP_TEST_RECIPIENT in .env")
    try:
        phone = trainees.normalize_phone(phone)
    except ValueError as e:
        sys.exit(str(e))
    if args.timezone:
        try:
            ZoneInfo(args.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            sys.exit(f"Unknown timezone {args.timezone!r}, e.g. Europe/Warsaw")

    with SessionLocal() as session:
        try:
            trainee = trainees.find_by_phone(session, phone)
        except OperationalError:
            sys.exit("The database isn't up to date – run `uv run alembic upgrade head` first")
        created = trainee is None
        if created:
            trainee = Trainee(
                phone=phone,
                trainer=trainees.trainer_named(session, args.trainer or DEFAULT_TRAINER),
                name=DEFAULT_NAME,
                consent_date=dt.date.today(),
                timezone=DEFAULT_TIMEZONE,
                training_plan=DEFAULT_PLAN.read_text(encoding="utf-8"),
            )
            session.add(trainee)
        elif args.trainer:
            trainee.trainer = trainees.trainer_named(session, args.trainer)
        if args.name:
            trainee.name = args.name
        if args.plan:
            trainee.training_plan = args.plan.read_text(encoding="utf-8")
        if args.no_plan:
            trainee.training_plan = None
        if args.consent_date:
            trainee.consent_date = args.consent_date
        if args.timezone:
            trainee.timezone = args.timezone
        session.commit()

        plan_lines = len(trainee.training_plan.splitlines()) if trainee.training_plan else 0
        print(
            f"{'Registered' if created else 'Updated'} {trainee.name} ({trainee.phone}), "
            f"trainer {trainee.trainer.name}, plan: "
            f"{f'{plan_lines} lines' if plan_lines else 'none'}, "
            f"consent {trainee.consent_date}, {trainee.timezone}"
        )


if __name__ == "__main__":
    main()
