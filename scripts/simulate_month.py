"""Send trainees' months of reports (scenarios/*.yaml) to the real model and show what's saved.

Three trainees by default: Kasia (plans/example.yaml, A/B/C), Tomek (plans/upper_lower.yaml,
fixed weekdays), Ola (plans/beginner_fbw.yaml, two alternating workouts); two trainers. Their
messages are sent in time order through the bot's real reply flow (history, OpenAI, validation,
saving), each at its own simulated time, into a separate SQLite database – never wabot.db.
Nothing is sent to WhatsApp: the replies are printed. One OpenAI call per message (two if a
reply is retried), so 45 calls for all three.

The database is kept afterwards, so the saved months can be inspected (or used to build the
dashboard against). tests/test_month_scenario.py replays the same scenarios with the answers
a correct model gives, which are also in the YAML files.

Run from the project root (OPENAI_API_KEY and the other settings come from .env):
    uv run python scripts/simulate_month.py
    uv run python scripts/simulate_month.py --only tomek ola
    uv run python scripts/simulate_month.py --daily-updates-only --db other.db
"""

import argparse
import logging
import os
import sys
from pathlib import Path

DEFAULT_DB = Path("simulated_month.db")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"database file, recreated on every run (default {DEFAULT_DB})",
    )
    parser.add_argument(
        "--only", nargs="+", metavar="NAME", help="scenario file names, e.g. kasia (default: all)"
    )
    parser.add_argument(
        "--daily-updates-only",
        action="store_true",
        help="one row per day, a correction updates it (default: as in .env)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    # Windows consoles default to cp1252, which can't print Polish letters.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    if args.db.name.startswith("wabot.db"):
        sys.exit("Use another file than wabot.db: this database is recreated.")
    args.db.unlink(missing_ok=True)
    # Before wabot is imported: the engine is created at import time; this overrides .env.
    os.environ["DATABASE_URL"] = f"sqlite:///{args.db}"

    from sqlalchemy import select

    from wabot import main as app
    from wabot import simulation
    from wabot.config import get_settings
    from wabot.db import SessionLocal, engine
    from wabot.models import Base, TrainingCheckin, WeightEntry

    # Only warnings (e.g. a reply that failed validation and was retried), not every request.
    logging.getLogger().setLevel(logging.WARNING)

    def print_saved(message_id: int) -> None:
        """What the reply to the message saved."""
        with SessionLocal() as session:
            for w in session.scalars(
                select(WeightEntry).where(WeightEntry.message_id == message_id)
            ):
                print(f"     saved weight: {w.date} {w.kg} kg")
            for c in session.scalars(
                select(TrainingCheckin).where(TrainingCheckin.message_id == message_id)
            ):
                print(f"     saved training: {c.date} {c.plan_day or '(outside plan)'}, {c.status}")
                for e in c.exercises:
                    dose = [e.sets and f"{e.sets}x", e.reps, e.weight_kg and f"{e.weight_kg:g} kg"]
                    dose = " ".join(str(x) for x in dose if x)
                    instead = f" (zamiast {e.replaces})" if e.replaces else ""
                    print(f"       - {e.exercise_id or '?'}: {e.name} {dose}{instead}".rstrip())
                if c.missed_exercises:
                    print(f"       missed: {', '.join(c.missed_exercises)}")
                if c.notes:
                    print(f"       notes: {c.notes}")

    def current_by_day(model, trainee_id: int) -> dict:
        """The newest row per day: the current value, what the dashboard will read."""
        with SessionLocal() as session:
            rows = session.scalars(
                select(model).where(model.trainee_id == trainee_id).order_by(model.id)
            )
            return {row.date: row for row in rows}

    scenarios = simulation.load_all()
    if args.only:
        if unknown := set(args.only) - {s.key for s in scenarios}:
            sys.exit(f"Not in scenarios/: {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s.key in args.only]

    Base.metadata.create_all(engine)
    settings = get_settings()
    if args.daily_updates_only:
        settings = settings.model_copy(update={"daily_updates_only": True})
    if not settings.openai_api_key:
        sys.exit("OPENAI_API_KEY is not set in .env")
    app.get_settings = lambda: settings

    registered = {s.key: simulation.register(s) for s in scenarios}
    print(
        f"model {settings.openai_model}, history {settings.history_mode}, "
        f"daily_updates_only={settings.daily_updates_only}"
    )
    for s in scenarios:
        print(f"  {s.name} ({s.trainer}): {s.plan_path}")
    print()

    with simulation.Replay() as replay:
        for i, (at, scenario, report) in enumerate(simulation.timeline(scenarios), 1):
            message_id = replay.send(registered[scenario.key], report["text"], at)
            print(f"[{i:2}] {report['at']}  {scenario.name}: {report['text']}")
            print("     bot: " + replay.replies[-1].replace("\n", "\n          "))
            print_saved(message_id)
            print()

    for s in scenarios:
        weights = current_by_day(WeightEntry, registered[s.key].id)
        days = current_by_day(TrainingCheckin, registered[s.key].id)
        print(f"{s.name} – current values per day:")
        for day in sorted(weights.keys() | days.keys()):
            parts = [f"{weights[day].kg} kg"] if day in weights else []
            if c := days.get(day):
                missed = f", missed {len(c.missed_exercises)}" if c.missed_exercises else ""
                label = (c.plan_day or "outside the plan").split(" (")[0]
                parts.append(f"{label}: {c.status}{missed}")
            print(f"  {day} {day:%a}  " + "; ".join(parts))
        print()
    print(f"Saved in {args.db}")


if __name__ == "__main__":
    main()
