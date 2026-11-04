import datetime as dt

import pytest
from sqlalchemy import func, select

from wabot import entries
from wabot.db import SessionLocal
from wabot.llm_output import Exercise, LlmOutput, Training, Weight
from wabot.models import Message, TrainingCheckin, TrainingExercise, WeightEntry

from .test_webhook import PHONE, add_trainee

MONDAY = dt.date(2026, 11, 2)


@pytest.fixture
def trainee():
    return add_trainee()


@pytest.fixture
def report(trainee):
    """Store a trainee's message, as the webhook does; returns it."""

    def store(body: str = "yanek ...") -> Message:
        with SessionLocal() as session:
            msg = Message(
                direction="in", phone=PHONE, type="text", body=body, trainee_id=trainee.id
            )
            session.add(msg)
            session.commit()
            return msg

    return store


def save(trainee, message, weight=None, training=None, daily=False) -> None:
    """entries.save(); daily is DAILY_UPDATES_ONLY."""
    with SessionLocal() as session:
        output = LlmOutput(reply="Zapisałem.", weight=weight, training=training)
        entries.save(session, trainee, message, output, daily_updates_only=daily)


def weights() -> list[tuple[dt.date, float, int]]:
    with SessionLocal() as session:
        rows = session.scalars(select(WeightEntry).order_by(WeightEntry.date))
        return [(e.date, e.kg, e.message_id) for e in rows]


def exercise(name: str, exercise_id: str | None, **fields) -> Exercise:
    defaults = dict(in_plan=True, sets=None, reps=None, weight_kg=None, replaces=None, comment=None)
    return Exercise(exercise_id=exercise_id, name=name, **{**defaults, **fields})


def training(*exercises: Exercise, status="done", missed=(), notes=None) -> Training:
    return Training(
        date=MONDAY,
        plan_day="Dzień A",
        status=status,
        exercises=list(exercises),
        missed_exercises=list(missed),
        notes=notes,
    )


def checkins() -> list[TrainingCheckin]:
    with SessionLocal() as session:
        rows = session.scalars(select(TrainingCheckin))
        # Load the exercises while the session is open.
        return [row for row in rows if row.exercises is not None]


def test_weight_is_saved_for_its_day(trainee, report):
    msg = report("yanek 80,2")
    save(trainee, msg, weight=Weight(kg=80.2, date=MONDAY))
    assert weights() == [(MONDAY, 80.2, msg.id)]
    with SessionLocal() as session:
        entry = session.scalar(select(WeightEntry))
        assert (entry.trainee_id, entry.trainer_id) == (trainee.id, trainee.trainer_id)


def test_by_default_a_correction_is_a_new_row(trainee, report):
    first, correction = report("yanek 80"), report("yanek sorki, 81")
    save(trainee, first, weight=Weight(kg=80, date=MONDAY))
    save(trainee, correction, weight=Weight(kg=81, date=MONDAY))
    assert weights() == [(MONDAY, 80, first.id), (MONDAY, 81, correction.id)]


def test_daily_updates_only_replaces_the_days_weight(trainee, report):
    first, correction = report("yanek 80"), report("yanek sorki, 81")
    save(trainee, first, weight=Weight(kg=80, date=MONDAY), daily=True)
    save(trainee, correction, weight=Weight(kg=81, date=MONDAY), daily=True)
    assert weights() == [(MONDAY, 81, correction.id)]


def test_daily_updates_only_updates_the_newest_of_several_rows(trainee, report):
    # Rows saved while DAILY_UPDATES_ONLY was off.
    first, second, third = report(), report(), report()
    save(trainee, first, weight=Weight(kg=80, date=MONDAY))
    save(trainee, second, weight=Weight(kg=81, date=MONDAY))
    save(trainee, third, weight=Weight(kg=82, date=MONDAY), daily=True)
    assert weights() == [(MONDAY, 80, first.id), (MONDAY, 82, third.id)]


def test_another_day_gets_its_own_weight(trainee, report):
    msg = report()
    save(trainee, msg, weight=Weight(kg=80, date=MONDAY))
    save(trainee, msg, weight=Weight(kg=79.5, date=MONDAY + dt.timedelta(days=1)))
    assert [kg for _, kg, _ in weights()] == [80, 79.5]


def test_an_older_report_does_not_overwrite_a_newer_one(trainee, report):
    # Replies can finish out of order: the reply to the newer message is saved first.
    older, newer = report("yanek 80"), report("yanek sorki, 81")
    save(trainee, newer, weight=Weight(kg=81, date=MONDAY), daily=True)
    save(trainee, older, weight=Weight(kg=80, date=MONDAY), daily=True)
    assert weights() == [(MONDAY, 81, newer.id)]


def test_each_trainee_has_their_own_day(trainee, report):
    other = add_trainee(phone="48999888777")
    msg = report()
    save(trainee, msg, weight=Weight(kg=80, date=MONDAY))
    save(other, msg, weight=Weight(kg=64, date=MONDAY))
    assert sorted(kg for _, kg, _ in weights()) == [64, 80]


def test_training_is_saved_with_its_exercises(trainee, report):
    msg = report("yanek A zrobione, przysiad 4x8 60, suwnica zamiast hip thrustu")
    save(
        trainee,
        msg,
        training=training(
            exercise("Przysiad ze sztangą", "back_squat", sets=4, reps="8", weight_kg=60),
            exercise("suwnica", "leg_press", in_plan=False, replaces="hip_thrust"),
            status="partial",
            missed=["plank"],
            notes="Bolało kolano.",
        ),
    )
    [checkin] = checkins()
    assert (checkin.date, checkin.plan_day, checkin.status) == (MONDAY, "Dzień A", "partial")
    assert (checkin.missed_exercises, checkin.notes) == (["plank"], "Bolało kolano.")
    assert (checkin.message_id, checkin.trainer_id) == (msg.id, trainee.trainer_id)
    squat, leg_press = checkin.exercises
    assert (squat.exercise_id, squat.sets, squat.reps, squat.weight_kg) == (
        "back_squat",
        4,
        "8",
        60,
    )
    assert (leg_press.in_plan, leg_press.replaces) == (False, "hip_thrust")


def save_training_and_correction(trainee, report, daily):
    first, correction = report(), report("yanek sorki, przysiad był na 65 kg")
    squat = exercise("Przysiad ze sztangą", "back_squat", sets=4, reps="8", weight_kg=60)
    hip_thrust = exercise("Hip thrust", "hip_thrust")
    save(
        trainee,
        first,
        training=training(squat, hip_thrust, status="partial", missed=["plank"]),
        daily=daily,
    )
    # The LLM returns the whole session again, with the fix.
    squat_65 = squat.model_copy(update={"weight_kg": 65})
    save(trainee, correction, training=training(squat_65, hip_thrust), daily=daily)


def test_by_default_a_corrected_training_is_a_new_session(trainee, report):
    save_training_and_correction(trainee, report, daily=False)
    first, corrected = checkins()
    assert [e.weight_kg for e in first.exercises] == [60, None]
    assert [e.weight_kg for e in corrected.exercises] == [65, None]
    assert (first.status, corrected.status) == ("partial", "done")


def test_daily_updates_only_replaces_the_whole_session(trainee, report):
    save_training_and_correction(trainee, report, daily=True)

    [checkin] = checkins()
    assert (checkin.status, checkin.missed_exercises) == ("done", [])
    assert [(e.name, e.weight_kg) for e in checkin.exercises] == [
        ("Przysiad ze sztangą", 65),
        ("Hip thrust", None),
    ]
    with SessionLocal() as session:
        # The first report's exercise rows are gone, not orphaned.
        assert session.scalar(select(func.count()).select_from(TrainingExercise)) == 2


def test_nothing_reported_saves_nothing(trainee, report):
    save(trainee, report("yanek co mam w środę?"))
    assert weights() == []
    assert checkins() == []


def test_exercise_columns_match_the_llm_output():
    columns = set(TrainingExercise.__table__.columns.keys())
    assert set(Exercise.model_fields) <= columns
