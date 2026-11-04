"""Three trainees' months of reports (scenarios/*.yaml, three plans, two trainers), replayed
through the real reply flow in the order they're sent: main.reply() → the history →
assistant._complete() (a fake OpenAI client returns the answer the scenario gives) →
validate_output() (plan checks, missed exercises, done/partial) → entries.save().

After every message it checks what was saved against the scenario. scripts/simulate_month.py
sends the same messages to the real model.
"""

import json
from collections import Counter
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from wabot import assistant, exercises, main, simulation
from wabot.config import Settings
from wabot.db import SessionLocal
from wabot.models import TrainingCheckin, WeightEntry
from wabot.plans import TrainingPlan

SCENARIOS = simulation.load_all()


def plan_day(plan: TrainingPlan, start: str):
    [day] = [d for d in plan.days if d.name.startswith(start)]
    return day


def answer(scenario: simulation.Scenario, report: dict) -> dict:
    """The JSON a correct model returns for the report (format in scenarios/kasia.yaml)."""
    today = scenario.time_of(report).astimezone(simulation.ZoneInfo(scenario.timezone)).date()
    out = {"reply": f"Zapisałem: {report['text']}", "weight": None, "training": None}

    if (weight := report.get("weight")) is not None:
        weight = weight if isinstance(weight, dict) else {"kg": weight}
        out["weight"] = {"kg": weight["kg"], "date": str(weight.get("date", today))}

    if (training := report.get("training")) is not None:
        day = plan_day(scenario.plan, training["day"]) if "day" in training else None
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


@pytest.fixture
def fake_llm(monkeypatch):
    """OpenAI replaced by a client that returns the answers queued in `answers`."""
    llm = SimpleNamespace(answers=[], requests=[])

    class FakeOpenAI:
        def __init__(self, api_key):
            self.responses = self

        def create(self, **request):
            llm.requests.append(request)
            return SimpleNamespace(output=[], output_text=json.dumps(llm.answers.pop(0)))

    monkeypatch.setattr(assistant.openai, "OpenAI", FakeOpenAI)
    return llm


def saved_for(model, message_id: int):
    with SessionLocal() as session:
        rows = session.scalars(select(model).where(model.message_id == message_id)).all()
        for row in rows:
            if isinstance(row, TrainingCheckin):
                row.saved_exercises = [(e.exercise_id, e.replaces) for e in row.exercises]
        return rows


def test_there_are_three_trainees_on_three_plans():
    assert len(SCENARIOS) == 3
    assert len({s.plan_path for s in SCENARIOS}) == 3
    for scenario in SCENARIOS:
        assert len(scenario.reports) == 15, scenario.key


@pytest.mark.parametrize("daily_updates_only", [False, True])
def test_a_month_of_reports_from_three_trainees(fake_llm, monkeypatch, daily_updates_only):
    settings = Settings(daily_updates_only=daily_updates_only)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    registered = {s.key: simulation.register(s) for s in SCENARIOS}
    texts = {s.key: {r["text"] for r in s.reports} for s in SCENARIOS}

    with simulation.Replay() as replay:
        for at, scenario, report in simulation.timeline(SCENARIOS):
            expected = answer(scenario, report)
            fake_llm.answers.append(expected)
            message_id = replay.send(registered[scenario.key], report["text"], at)
            where = f"{scenario.key} {report['at']}"

            # The model got this trainee's messages only, and the simulated date.
            request = fake_llm.requests[-1]
            turns = [t["content"] for t in request["input"] if t["role"] == "user"]
            assert turns[-1] == report["text"], where
            assert set(turns) <= texts[scenario.key], where
            local_date = at.astimezone(simulation.ZoneInfo(scenario.timezone)).date()
            assert str(local_date) in request["input"][-1]["content"], where
            assert replay.replies[-1] == expected["reply"], where

            weights = saved_for(WeightEntry, message_id)
            if expected["weight"]:
                [weight] = weights
                assert (weight.kg, str(weight.date)) == (
                    expected["weight"]["kg"],
                    expected["weight"]["date"],
                ), where
            else:
                assert weights == [], where

            checkins = saved_for(TrainingCheckin, message_id)
            if expected["training"]:
                [checkin] = checkins
                training = expected["training"]
                saved = report.get("saved", {})
                assert (checkin.plan_day, str(checkin.date)) == (
                    training["plan_day"],
                    training["date"],
                ), where
                assert checkin.status == saved.get("status", training["status"]), where
                assert checkin.missed_exercises == saved.get("missed", []), where
                assert checkin.saved_exercises == [
                    (e["exercise_id"], e["replaces"]) for e in training["exercises"]
                ], where
            else:
                assert checkins == [], where

    # A correction is an extra row, unless DAILY_UPDATES_ONLY: then one row per day.
    with SessionLocal() as session:
        for model in (WeightEntry, TrainingCheckin):
            rows = Counter(session.execute(select(model.trainee_id, model.date)).all())
            if daily_updates_only:
                assert set(rows.values()) == {1}, model.__name__
            else:
                assert max(rows.values()) == 2, model.__name__
