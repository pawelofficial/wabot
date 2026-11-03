"""Saving what the LLM extracted from a report: weight_entries and training_checkins.

There is one weight and one training per trainee per day. A report for a day that already has
one replaces it, which is how corrections work: "yanek 80 kg", then "yanek sorki, 81" saves 81.
For a training the LLM returns the whole corrected session (prompts.yaml), so replacing it
keeps the exercises reported earlier.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot.llm_output import LlmOutput, Training, Weight
from wabot.models import Message, Trainee, TrainingCheckin, TrainingExercise, WeightEntry


def save(session: Session, trainee: Trainee, message: Message, output: LlmOutput) -> None:
    """Save the weight and training in output, reported in message (the one the bot answers)."""
    if output.weight is not None:
        _save_weight(session, trainee, message, output.weight)
    if output.training is not None:
        _save_training(session, trainee, message, output.training)
    session.commit()


def _entry_for_day(
    session: Session, model: type[WeightEntry | TrainingCheckin], trainee: Trainee, day: dt.date
):
    return session.scalar(select(model).where(model.trainee_id == trainee.id, model.date == day))


def _newer_report_saved(entry: WeightEntry | TrainingCheckin, message: Message) -> bool:
    # Replies to messages sent close together can finish out of order; an older report must
    # not overwrite a newer one. (Message IDs grow in the order messages arrive.)
    return entry.message_id is not None and entry.message_id > message.id


def _save_weight(session: Session, trainee: Trainee, message: Message, weight: Weight) -> None:
    entry = _entry_for_day(session, WeightEntry, trainee, weight.date)
    if entry is None:
        entry = WeightEntry(trainer_id=trainee.trainer_id, trainee_id=trainee.id, date=weight.date)
        session.add(entry)
    elif _newer_report_saved(entry, message):
        return
    entry.kg = weight.kg
    entry.message_id = message.id


def _save_training(
    session: Session, trainee: Trainee, message: Message, training: Training
) -> None:
    checkin = _entry_for_day(session, TrainingCheckin, trainee, training.date)
    if checkin is None:
        checkin = TrainingCheckin(
            trainer_id=trainee.trainer_id, trainee_id=trainee.id, date=training.date
        )
        session.add(checkin)
    elif _newer_report_saved(checkin, message):
        return
    checkin.plan_day = training.plan_day
    checkin.status = training.status
    checkin.missed_exercises = list(training.missed_exercises)
    checkin.notes = training.notes
    checkin.message_id = message.id
    # The columns have the same names as the fields of llm_output.Exercise.
    checkin.exercises = [TrainingExercise(**ex.model_dump()) for ex in training.exercises]
