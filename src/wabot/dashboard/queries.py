"""What the dashboard pages read from the database.

Every report is kept as its own row, and the newest row of a day (highest id) is that day's
current value (docs/PLAN.md, "Every report is kept"). current_per_day() applies that rule, so
the pages don't each repeat it.
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wabot.models import Message, Trainee, WeightEntry


def current_per_day(session: Session, model, trainee: Trainee) -> list:
    """The trainee's newest row of each date of `model` (WeightEntry, TrainingCheckin), oldest
    date first."""
    newest = (
        select(func.max(model.id))
        .where(model.trainee_id == trainee.id, model.trainer_id == trainee.trainer_id)
        .group_by(model.date)
    )
    return list(session.scalars(select(model).where(model.id.in_(newest)).order_by(model.date)))


@dataclass
class WeighIn:
    date: dt.date
    kg: float
    message: str | None  # the WhatsApp message the weight was saved from
    reports: int  # how many reports that day had (more than 1: the trainee corrected it)

    def as_json(self) -> dict:
        return {"date": self.date.isoformat(), "kg": self.kg, "message": self.message}


def weigh_ins(session: Session, trainee: Trainee) -> list[WeighIn]:
    """The trainee's current weight of each day, with the message it came from."""
    entries = current_per_day(session, WeightEntry, trainee)
    messages = dict(
        session.execute(
            select(Message.id, Message.body).where(
                Message.id.in_([e.message_id for e in entries if e.message_id])
            )
        ).all()
    )
    reports = dict(
        session.execute(
            select(WeightEntry.date, func.count())
            .where(WeightEntry.trainee_id == trainee.id)
            .group_by(WeightEntry.date)
        ).all()
    )
    return [
        WeighIn(e.date, e.kg, messages.get(e.message_id), reports.get(e.date, 1)) for e in entries
    ]
