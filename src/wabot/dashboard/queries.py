"""What the dashboard pages read from the database.

Every report is kept as its own row, and the newest row of a day (highest id) is that day's
current value (docs/PLAN.md, "Every report is kept"); for trainings, the newest of each kind, so
a day can have a training of the plan and one outside it (entries.outside_plan()).
current_per_day() and workouts() apply that rule, so the pages don't each repeat it.
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from wabot import exercises
from wabot.models import Message, Trainee, TraineePlan, TrainingCheckin, WeightEntry
from wabot.plans import TrainingPlan


def _newest_ids(model, trainee: Trainee, *keys):
    """The IDs of the trainee's newest row of each date (and each value of `keys`)."""
    return (
        select(func.max(model.id))
        .where(model.trainee_id == trainee.id, model.trainer_id == trainee.trainer_id)
        .group_by(model.date, *keys)
    )


def current_per_day(session: Session, model, trainee: Trainee) -> list:
    """The trainee's newest row of each date of `model` (WeightEntry), oldest date first."""
    newest = _newest_ids(model, trainee)
    return list(session.scalars(select(model).where(model.id.in_(newest)).order_by(model.date)))


def _messages(session: Session, rows) -> dict[int, Message]:
    """The messages the rows were saved from, by ID."""
    ids = [r.message_id for r in rows if r.message_id]
    return {m.id: m for m in session.scalars(select(Message).where(Message.id.in_(ids)))}


@dataclass
class WeighIn:
    date: dt.date
    kg: float
    message: str | None  # the message the weight was saved from
    reports: int  # how many reports that day had (more than 1: the trainee corrected it)

    def as_json(self) -> dict:
        return {"date": self.date.isoformat(), "kg": self.kg, "message": self.message}


def weigh_ins(session: Session, trainee: Trainee) -> list[WeighIn]:
    """The trainee's current weight of each day, with the message it came from."""
    entries = current_per_day(session, WeightEntry, trainee)
    messages = _messages(session, entries)
    reports = dict(
        session.execute(
            select(WeightEntry.date, func.count())
            .where(WeightEntry.trainee_id == trainee.id)
            .group_by(WeightEntry.date)
        ).all()
    )
    return [
        WeighIn(
            e.date,
            e.kg,
            messages[e.message_id].body if e.message_id in messages else None,
            reports.get(e.date, 1),
        )
        for e in entries
    ]


@dataclass
class Workout:
    """One of a day's current trainings: the newest report of its kind (the plan's, or outside
    the plan), with what the calendar shows about it."""

    checkin: TrainingCheckin
    message: Message | None  # the message it was saved from
    reports: int  # reports of this kind that day (more than 1: the trainee corrected it)
    # Its plan day's letter (A): in the current plan if the day is still in it (found by the
    # day's ID, so a renamed day keeps its letter), else in the version it was saved against.
    # None outside the plan and when the day isn't known.
    label: str | None
    # The day's name in the current plan, when the trainer renamed it since.
    current_name: str | None
    # How many exercises its plan day had (the "5" of "4/5"); None without a known day.
    planned: int | None

    @property
    def kind(self) -> str:
        """done, partial, skipped or outside (the CSS class and the legend's entry)."""
        return "outside" if self.checkin.outside_plan else self.checkin.status

    @property
    def done(self) -> int | None:
        """How many of the plan day's exercises were done (or replaced)."""
        return None if self.planned is None else self.planned - len(self.checkin.missed_exercises)

    @property
    def missed_names(self) -> list[str]:
        return [exercises.BY_ID[i].name for i in self.checkin.missed_exercises]

    @property
    def mark(self) -> str:
        """What the calendar cell shows under the letter."""
        if self.kind == "partial" and self.planned is not None:
            return f"{self.done}/{self.planned}"
        return {"done": "✓", "partial": "◐", "skipped": "✕", "outside": "+"}[self.kind]

    @property
    def status_text(self) -> str:
        if self.kind == "partial" and self.planned is not None:
            return f"częściowo, {self.done} z {self.planned} ćwiczeń"
        return {
            "done": "zrobiony",
            "partial": "częściowo",
            "skipped": "pominięty",
            "outside": "poza planem",
        }[self.kind]

    @property
    def title(self) -> str:
        """The training's name: its plan day, or what it was outside the plan."""
        if self.checkin.outside_plan:
            names = ", ".join(e.name for e in self.checkin.exercises)
            return f"Poza planem: {names}" if names else "Trening poza planem"
        return self.checkin.plan_day or "Trening z planu, dzień nieznany"

    @property
    def summary(self) -> str:
        """One line for the calendar cell's tooltip and screen readers."""
        if self.checkin.outside_plan:
            return self.title
        label = f"{self.label} · " if self.label else ""
        return f"{label}{self.title}: {self.status_text}"


def workouts(session: Session, trainee: Trainee, first: dt.date, last: dt.date) -> list[Workout]:
    """The trainee's current trainings from `first` to `last` (inclusive), by date, the plan's
    before the one outside it. A day has at most two: the newest report of each kind."""
    in_range = (
        TrainingCheckin.trainee_id == trainee.id,
        TrainingCheckin.date >= first,
        TrainingCheckin.date <= last,
    )
    newest = _newest_ids(TrainingCheckin, trainee, TrainingCheckin.outside_plan).where(*in_range)
    checkins = list(
        session.scalars(
            select(TrainingCheckin)
            .where(TrainingCheckin.id.in_(newest))
            .options(selectinload(TrainingCheckin.exercises))
            .order_by(TrainingCheckin.date, TrainingCheckin.outside_plan)
        )
    )
    reports = {
        (day, outside): n
        for day, outside, n in session.execute(
            select(TrainingCheckin.date, TrainingCheckin.outside_plan, func.count())
            .where(*in_range)
            .group_by(TrainingCheckin.date, TrainingCheckin.outside_plan)
        )
    }
    messages = _messages(session, checkins)
    # The plan versions the trainings were checked against (each the trainee's own).
    versions = {
        v.id: TrainingPlan.model_validate(v.plan)
        for v in session.scalars(
            select(TraineePlan).where(
                TraineePlan.trainee_id == trainee.id,
                TraineePlan.id.in_({c.plan_version_id for c in checkins if c.plan_version_id}),
            )
        )
    }
    current_version = trainee.plan_version
    current = versions.get(current_version.id) if current_version else None
    if current_version is not None and current is None:
        current = TrainingPlan.model_validate(current_version.plan)
    current_labels = current.day_labels() if current else {}
    current_names = {d.id: d.name for d in current.days} if current else {}

    found = []
    for c in checkins:
        version = versions.get(c.plan_version_id)
        day = (
            next((d for d in version.days if d.id == c.plan_day_id), None)
            if version is not None and c.plan_day_id
            else None
        )
        if c.plan_day_id in current_labels:
            label = current_labels[c.plan_day_id]
            renamed = current_names[c.plan_day_id] != c.plan_day
            current_name = current_names[c.plan_day_id] if renamed else None
        else:
            label = version.day_labels()[day.id] if day is not None else None
            current_name = None
        found.append(
            Workout(
                checkin=c,
                message=messages.get(c.message_id),
                reports=reports.get((c.date, c.outside_plan), 1),
                label=label,
                current_name=current_name,
                planned=len(day.exercises) if day is not None else None,
            )
        )
    return found


def last_training_date(session: Session, trainee: Trainee) -> dt.date | None:
    return session.scalar(
        select(func.max(TrainingCheckin.date)).where(TrainingCheckin.trainee_id == trainee.id)
    )
