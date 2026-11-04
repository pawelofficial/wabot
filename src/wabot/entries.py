"""Saving what the LLM extracted from a report: weight_entries and training_checkins.

Two modes, chosen by DAILY_UPDATES_ONLY (config.py):

- False (default): every report is a new row, nothing is overwritten. "yanek 80 kg", then
  "yanek sorki, 81" leaves both rows; the current value for a day is its newest row (highest
  id), which is what the dashboard and reports should read.
- True: one weight and one training per trainee per day. A report for a day that already has
  one replaces it: the same two messages leave one row, 81.

Either way, for a correction of a training the LLM returns the whole corrected session
(prompts.yaml), so the newest row for the day has everything.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot.llm_output import LlmOutput, Training, Weight
from wabot.models import Message, Trainee, TrainingCheckin, TrainingExercise, WeightEntry


def save(
    session: Session,
    trainee: Trainee,
    message: Message,
    output: LlmOutput,
    daily_updates_only: bool = False,
) -> None:
    """Save the weight and training in output, reported in message (the one the bot answers).
    daily_updates_only: update the day's entry instead of adding a new one."""
    if output.weight is not None:
        _save_weight(session, trainee, message, output.weight, daily_updates_only)
    if output.training is not None:
        _save_training(session, trainee, message, output.training, daily_updates_only)
    session.commit()


def _entry_for_day(
    session: Session, model: type[WeightEntry | TrainingCheckin], trainee: Trainee, day: dt.date
):
    """The day's newest entry (several exist if DAILY_UPDATES_ONLY was off at some point)."""
    return session.scalar(
        select(model)
        .where(model.trainee_id == trainee.id, model.date == day)
        .order_by(model.id.desc())
        .limit(1)
    )


def _newer_report_saved(entry: WeightEntry | TrainingCheckin, message: Message) -> bool:
    # Replies to messages sent close together can finish out of order; an older report must
    # not overwrite a newer one. (Message IDs grow in the order messages arrive.)
    return entry.message_id is not None and entry.message_id > message.id


def _entry_to_fill(
    session: Session,
    model: type[WeightEntry | TrainingCheckin],
    trainee: Trainee,
    message: Message,
    day: dt.date,
    daily_updates_only: bool,
):
    """The row to write the report into, or None to leave the day as it is."""
    if daily_updates_only:
        entry = _entry_for_day(session, model, trainee, day)
        if entry is not None:
            return None if _newer_report_saved(entry, message) else entry
    entry = model(trainer_id=trainee.trainer_id, trainee_id=trainee.id, date=day)
    session.add(entry)
    return entry


def _save_weight(
    session: Session, trainee: Trainee, message: Message, weight: Weight, daily_updates_only: bool
) -> None:
    entry = _entry_to_fill(session, WeightEntry, trainee, message, weight.date, daily_updates_only)
    if entry is None:
        return
    entry.kg = weight.kg
    entry.message_id = message.id


def _save_training(
    session: Session,
    trainee: Trainee,
    message: Message,
    training: Training,
    daily_updates_only: bool,
) -> None:
    checkin = _entry_to_fill(
        session, TrainingCheckin, trainee, message, training.date, daily_updates_only
    )
    if checkin is None:
        return
    checkin.plan_day = training.plan_day
    checkin.status = training.status
    checkin.missed_exercises = list(training.missed_exercises)
    checkin.notes = training.notes
    checkin.message_id = message.id
    # The columns have the same names as the fields of llm_output.Exercise. Replacing the list
    # of an updated check-in deletes its old exercise rows (delete-orphan).
    checkin.exercises = [TrainingExercise(**ex.model_dump()) for ex in training.exercises]
