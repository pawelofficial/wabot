"""Three trainees' months of reports (scenarios/*.yaml, three plans, two trainers), replayed
through the real reply flow in the order they're sent: main.reply() → the history →
assistant._complete() (simulation.canned_llm(): the answer the scenario gives) →
validate_output() (plan checks, missed exercises, done/partial) → entries.save().

After every message it checks what was saved against the scenario. scripts/simulate_month.py
sends the same messages to the real model.
"""

from collections import Counter

import pytest
from sqlalchemy import select

from wabot import main, plan_history, plans, simulation
from wabot.config import Settings
from wabot.db import SessionLocal
from wabot.models import (
    PlanTemplate,
    Trainee,
    TraineePlan,
    Trainer,
    TrainingCheckin,
    WeightEntry,
)

SCENARIOS = simulation.load_all()


@pytest.fixture
def fake_llm():
    with simulation.canned_llm() as llm:
        yield llm


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


def test_simulated_trainers_and_trainees_have_every_field_filled():
    """The simulated people must look like real ones, so the dashboard works on the simulation's
    data: a new column (like trainers.email for signing in) needs a value in scenarios/ too."""
    for scenario in SCENARIOS:
        simulation.register(scenario)
    with SessionLocal() as session:
        for model in (Trainer, Trainee, PlanTemplate, TraineePlan):
            rows = session.scalars(select(model)).all()
            assert rows, f"the scenarios make no {model.__name__}"
            for row in rows:
                empty = [c.name for c in model.__table__.columns if getattr(row, c.name) is None]
                assert empty == [], f"{model.__name__} {row.id}: {empty} not set by the scenarios"
        # Kasia and Tomek are both Marek's, with different plans: two library plans for him.
        assert len(plan_history.library(session, 1)) == 2
        # Every stored plan day has its ID, and a trainee's days have their library plan's.
        for row in [*session.scalars(select(PlanTemplate)), *session.scalars(select(TraineePlan))]:
            assert all(day.get("id") for day in row.plan["days"]), row
        for version in session.scalars(select(TraineePlan)):
            library_plan = session.get(PlanTemplate, version.template_id)
            assert version.plan == library_plan.plan


@pytest.mark.parametrize("daily_updates_only", [False, True])
def test_a_month_of_reports_from_three_trainees(fake_llm, monkeypatch, daily_updates_only):
    settings = Settings(daily_updates_only=daily_updates_only)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    registered = {s.key: simulation.register(s) for s in SCENARIOS}
    texts = {s.key: {r["text"] for r in s.reports} for s in SCENARIOS}

    with simulation.Replay() as replay:
        for at, scenario, report in simulation.timeline(SCENARIOS):
            expected = simulation.correct_answer(scenario, report)
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
                # Checked against the trainee's plan version (the one from the library), and the
                # day's ID from it.
                trainee = registered[scenario.key]
                assert checkin.plan_version_id == trainee.plan_version_id, where
                day = plans.of(trainee).day(training["plan_day"]) if training["plan_day"] else None
                assert checkin.plan_day_id == (day.id if day else None), where
                assert checkin.missed_exercises == saved.get("missed", []), where
                assert checkin.saved_exercises == [
                    (e["exercise_id"], e["replaces"]) for e in training["exercises"]
                ], where
            else:
                assert checkins == [], where
            # Saved at the simulated time, like the messages.
            for entry in [*weights, *checkins]:
                assert entry.updated_at == at, where

    # A correction is an extra row, unless DAILY_UPDATES_ONLY: then one row per day.
    with SessionLocal() as session:
        for model in (WeightEntry, TrainingCheckin):
            rows = Counter(session.execute(select(model.trainee_id, model.date)).all())
            if daily_updates_only:
                assert set(rows.values()) == {1}, model.__name__
            else:
                assert max(rows.values()) == 2, model.__name__
