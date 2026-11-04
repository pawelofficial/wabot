"""Replaying trainees' months of reports (scenarios/*.yaml) through the bot's real reply flow
at simulated times. Used by tests/test_month_scenario.py (with canned LLM answers) and
scripts/simulate_month.py (the real model, or the canned answers with --canned, to fill a
database for the dashboard); the app itself doesn't use it.

Each message is stored as the webhook stores it, with the simulated time as created_at, and
answered by main.reply(), which reads the history, calls the LLM, validates and saves. While
replaying, main.reply() sees the simulated time as "now", and replies are recorded and stored
at that time instead of being sent to WhatsApp.
"""

import contextlib
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import yaml

from wabot import assistant, conversation, exercises, main, plans, trainees
from wabot.db import SessionLocal
from wabot.models import Message, Trainee
from wabot.plans import TrainingPlan

SCENARIOS = Path("scenarios")


@dataclass
class Scenario:
    """One trainee's month: who they are, their plan file and their reports."""

    key: str  # the file name without .yaml
    name: str
    trainer: str
    trainer_email: str
    phone: str
    plan_path: Path
    reports: list[dict]
    timezone: str = "Europe/Warsaw"

    @property
    def plan(self) -> TrainingPlan:
        return plans.load(self.plan_path)

    def time_of(self, report: dict) -> dt.datetime:
        """When the report is sent, in UTC."""
        local = dt.datetime.fromisoformat(str(report["at"]))
        return local.replace(tzinfo=ZoneInfo(self.timezone)).astimezone(dt.UTC)


def load(path: Path) -> Scenario:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Scenario(
        key=path.stem,
        name=data["name"],
        trainer=data["trainer"],
        trainer_email=data["trainer_email"],
        phone=str(data["phone"]),
        plan_path=Path(data["plan"]),
        reports=data["reports"],
    )


def load_all(directory: Path = SCENARIOS) -> list[Scenario]:
    return [load(path) for path in sorted(directory.glob("*.yaml"))]


def timeline(scenarios: list[Scenario]) -> list[tuple[dt.datetime, Scenario, dict]]:
    """Every report of every scenario, in the order they're sent."""
    events = [(s.time_of(r), s, r) for s in scenarios for r in s.reports]
    return sorted(events, key=lambda event: event[0])


def register(scenario: Scenario) -> Trainee:
    """Add the scenario's trainee (and trainer) with their plan."""
    with SessionLocal() as session:
        trainer = trainees.trainer_named(session, scenario.trainer)
        email = trainees.normalize_email(scenario.trainer_email)
        if trainer.email not in (None, email):
            raise ValueError(f"{scenario.key}: {trainer.name} already has email {trainer.email}")
        trainer.email = email
        trainee = Trainee(
            trainer=trainer,
            phone=scenario.phone,
            name=scenario.name,
            timezone=scenario.timezone,
            consent_date=dt.date(2026, 10, 1),
            training_plan=plans.to_db(scenario.plan),
        )
        session.add(trainee)
        session.commit()
        return trainee


def _plan_day(plan: TrainingPlan, start: str):
    [day] = [d for d in plan.days if d.name.startswith(start)]
    return day


def correct_answer(scenario: Scenario, report: dict) -> dict:
    """The JSON a correct model returns for the report (format in scenarios/kasia.yaml)."""
    today = scenario.time_of(report).astimezone(ZoneInfo(scenario.timezone)).date()
    out = {"reply": f"Zapisałem: {report['text']}", "weight": None, "training": None}

    if (weight := report.get("weight")) is not None:
        weight = weight if isinstance(weight, dict) else {"kg": weight}
        out["weight"] = {"kg": weight["kg"], "date": str(weight.get("date", today))}

    if (training := report.get("training")) is not None:
        day = _plan_day(scenario.plan, training["day"]) if "day" in training else None
        items = training["exercises"]
        if items == "all":
            items = [e.id for e in day.exercises]
        planned = {e.id for e in day.exercises} if day else set()
        exercise_list = []
        for item in items:
            item = item if isinstance(item, dict) else {"id": item}
            exercise_id = item.get("id")
            exercise_list.append(
                {
                    "exercise_id": exercise_id,
                    "name": item.get("name") or exercises.BY_ID[exercise_id].name,
                    "in_plan": exercise_id in planned and "replaces" not in item,
                    "sets": item.get("sets"),
                    "reps": item.get("reps"),
                    "weight_kg": item.get("kg"),
                    "replaces": item.get("replaces"),
                    "comment": None,
                }
            )
        out["training"] = {
            "date": str(training.get("date", today)),
            "plan_day": day.name if day else None,
            "status": training.get("status", "done"),
            "exercises": exercise_list,
            "notes": training.get("notes"),
        }
    return out


@contextlib.contextmanager
def canned_llm():
    """OpenAI replaced by a client that returns the answers queued in `.answers`, in order;
    `.requests` collects what was sent to it."""
    llm = SimpleNamespace(answers=[], requests=[])

    class CannedOpenAI:
        def __init__(self, api_key):
            self.responses = self

        def create(self, **request):
            llm.requests.append(request)
            return SimpleNamespace(output=[], output_text=json.dumps(llm.answers.pop(0)))

    with mock.patch.object(assistant.openai, "OpenAI", CannedOpenAI):
        yield llm


class Replay:
    """Sends messages at simulated times. Use as a context manager: inside it, main.reply()
    runs at the simulated time and its replies go to `self.replies` instead of WhatsApp."""

    def __init__(self):
        self.now: dt.datetime | None = None
        self.replies: list[str] = []
        self._patches = contextlib.ExitStack()

    def __enter__(self):
        self._patches.enter_context(mock.patch.object(main, "utcnow", lambda: self.now))
        self._patches.enter_context(
            mock.patch.object(conversation, "send_and_store", self._send_and_store)
        )
        return self

    def __exit__(self, *exc):
        self._patches.close()

    def _send_and_store(self, settings, trainee, text):
        # As conversation.send_and_store(), but stored at the simulated time and not sent.
        self.replies.append(text)
        with SessionLocal() as session:
            session.add(
                Message(
                    direction="out",
                    phone=trainee.phone,
                    type="text",
                    body=text,
                    trainee_id=trainee.id,
                    trainer_id=trainee.trainer_id,
                    created_at=self.now,
                )
            )
            session.commit()
        return f"wamid.simulated-out{len(self.replies)}"

    def send(self, trainee: Trainee, text: str, at: dt.datetime) -> int:
        """The trainee sends text at `at` (UTC) and the bot answers. Returns the message's ID."""
        self.now = at
        with SessionLocal() as session:
            message = Message(
                wamid=f"wamid.simulated-in{trainee.id}-{at:%Y%m%d%H%M%S}",
                direction="in",
                phone=trainee.phone,
                type="text",
                body=text,
                trainee_id=trainee.id,
                trainer_id=trainee.trainer_id,
                created_at=at,
            )
            session.add(message)
            session.commit()
            message_id = message.id
        if conversation.mentions_bot(text):
            main.reply(message_id)
        return message_id
