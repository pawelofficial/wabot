"""Set up a database: create it, or bring an existing one up to date, with the migrations;
optionally add the simulated month (two trainers, three trainees, their plans and a month of
reports).

The database is DATABASE_URL from .env (default sqlite:///wabot.db): the same one the bot
(run.sh), the dashboard (run_dashboard.sh) and the scripts use. --new creates another SQLite
file instead and prints the line that points .env at it.

    uv run python scripts/setup_db.py                    # migrate DATABASE_URL's database
    uv run python scripts/setup_db.py --simulated        # ... and add the simulated month
    uv run python scripts/setup_db.py --new dev.db       # a new, empty database dev.db
    uv run python scripts/setup_db.py --new dev.db --simulated

--simulated uses the scenarios' correct answers (no OpenAI calls), like simulate_month.py
--canned, and refuses if the simulated trainees are already there. Before it changes an existing
SQLite database, a copy goes to db_backups/ next to it.
"""

import argparse
import datetime as dt
import logging
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--new", type=Path, metavar="FILE", help="create this SQLite file instead (must not exist)"
    )
    parser.add_argument(
        "--simulated",
        action="store_true",
        help="add the simulated trainers, trainees and their month (scenarios/*.yaml)",
    )
    return parser.parse_args(argv)


def backup(path: Path) -> Path:
    """A consistent copy of a SQLite database (SQLite's backup API, so the bot can keep running
    and nothing in its WAL file is missed)."""
    backups = path.parent / "db_backups"
    backups.mkdir(exist_ok=True)
    target = backups / f"{path.stem}-{dt.datetime.now():%Y%m%d-%H%M%S}{path.suffix}"
    source, copy = sqlite3.connect(path), sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        source.close()
        copy.close()
    return target


def main(argv: list[str] | None = None) -> None:
    # Windows consoles default to cp1252, which can't print Polish letters.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    args = parse_args(argv)
    if args.new is not None:
        if args.new.exists():
            sys.exit(f"{args.new} already exists – pick another name, or leave out --new")
        # Before wabot is imported: the engine is created at import time; this overrides .env.
        os.environ["DATABASE_URL"] = f"sqlite:///{args.new.as_posix()}"

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import func, select
    from sqlalchemy.engine import make_url

    from wabot import simulation
    from wabot.config import DatabaseSettings
    from wabot.db import SessionLocal
    from wabot.models import Message, PlanTemplate, Trainee, Trainer

    logging.getLogger().setLevel(logging.WARNING)
    url = make_url(DatabaseSettings().database_url)
    print(f"Database: {url.render_as_string(hide_password=True)}")
    sqlite_file = Path(url.database) if url.get_backend_name() == "sqlite" else None

    if args.simulated and sqlite_file is not None and sqlite_file.exists():
        print(f"Backup: {backup(sqlite_file)}")

    # No alembic.ini: its logging setup would silence the app's warnings.
    alembic = Config()
    alembic.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(alembic, "head")
    print("Migrated to the newest version.")

    if args.simulated:
        scenarios = simulation.load_all()
        with SessionLocal() as session:
            there = session.scalars(
                select(Trainee.name)
                .where(Trainee.phone.in_([s.phone for s in scenarios]))
                .order_by(Trainee.name)
            ).all()
        if there:
            sys.exit(f"The simulated trainees are already there ({', '.join(there)}).")
        simulation.load_canned(scenarios)
        print(f"Added the simulated month: {', '.join(s.name for s in scenarios)}.")

    with SessionLocal() as session:
        print("\nTrainers:")
        for trainer in session.scalars(select(Trainer).order_by(Trainer.name)):
            names = session.scalars(
                select(Trainee.name).where(Trainee.trainer_id == trainer.id).order_by(Trainee.name)
            ).all()
            library = session.scalar(
                select(func.count())
                .select_from(PlanTemplate)
                .where(PlanTemplate.trainer_id == trainer.id)
            )
            print(
                f"  {trainer.name} ({trainer.email or 'no email'}): "
                f"{', '.join(names) or 'no trainees'}; {library} plans in the library"
            )
        messages = session.scalar(select(func.count()).select_from(Message))
        print(f"Messages: {messages}")
    if args.new is not None:
        print(
            f"\nTo use it everywhere, put this into .env:\n  DATABASE_URL=sqlite:///{args.new.as_posix()}"
        )


if __name__ == "__main__":
    main()
