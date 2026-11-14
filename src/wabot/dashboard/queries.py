"""What the dashboard pages read from the database.

Every report is kept as its own row, and the newest row of a day (highest id) is that day's
current value (docs/PLAN.md, "Every report is kept"); for trainings, the newest of each kind, so
a day can have a training of the plan and one outside it (entries.outside_plan()).
current_per_day() and workouts() apply that rule, so the pages don't each repeat it.
"""

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from wabot import exercises
from wabot.models import (
    Message,
    Trainee,
    TraineePlan,
    TrainingCheckin,
    TrainingExercise,
    WeightEntry,
)
from wabot.plans import PlanDay, PlanExercise, TrainingPlan


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


def conversations(
    session: Session, trainee: Trainee, message_ids: list[int | None]
) -> dict[int, list[Message]]:
    """For each message an entry was saved from (the "yanek" a reply answered): that reply's
    conversation, oldest first. It's what the reply read as new, the trainee's messages it
    marked handled together (one `handled_at`, conversation.claim()) and anything in between,
    then the bot's answer (the first bot message after them). A message without `handled_at`
    (saved before migration 0011) is shown alone with the answer."""
    ids = {i for i in message_ids if i}
    if not ids:
        return {}
    own = (Message.trainee_id == trainee.id, Message.trainer_id == trainee.trainer_id)
    triggers = list(session.scalars(select(Message).where(Message.id.in_(ids), *own)))
    handled = {m.handled_at for m in triggers if m.handled_at is not None}
    turns = {
        at: (first, last)
        for at, first, last in session.execute(
            select(Message.handled_at, func.min(Message.id), func.max(Message.id))
            .where(*own, Message.direction == "in", Message.handled_at.in_(handled))
            .group_by(Message.handled_at)
        )
    }
    found = {}
    for trigger in triggers:
        first, last = turns.get(trigger.handled_at, (trigger.id, trigger.id))
        answer = session.scalar(
            select(func.min(Message.id)).where(*own, Message.direction == "out", Message.id > last)
        )
        found[trigger.id] = list(
            session.scalars(
                select(Message)
                .where(*own, Message.id >= first, Message.id <= (answer or last))
                .order_by(Message.id)
            )
        )
    return found


def _reports(session: Session, trainee: Trainee, rows, key) -> dict:
    """The conversations of every report of each `key(row)`, oldest report first."""
    by_message = conversations(session, trainee, [r.message_id for r in rows])
    grouped: dict = {}
    for row in sorted(rows, key=lambda r: r.id):
        if row.message_id in by_message:
            grouped.setdefault(key(row), []).append(by_message[row.message_id])
    return grouped


@dataclass
class WeighIn:
    date: dt.date
    kg: float
    message: str | None  # the message the weight was saved from
    reports: int  # how many reports that day had (more than 1: the trainee corrected it)
    # The conversation of each of the day's reports, oldest first (the last one counts).
    conversations: list[list[Message]] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"date": self.date.isoformat(), "kg": self.kg, "message": self.message}


def weigh_ins(session: Session, trainee: Trainee) -> list[WeighIn]:
    """The trainee's current weight of each day, with the message it came from."""
    entries = current_per_day(session, WeightEntry, trainee)
    messages = _messages(session, entries)
    every_report = list(
        session.scalars(
            select(WeightEntry).where(
                WeightEntry.trainee_id == trainee.id, WeightEntry.trainer_id == trainee.trainer_id
            )
        )
    )
    reports = Counter(e.date for e in every_report)
    talks = _reports(session, trainee, every_report, lambda e: e.date)
    return [
        WeighIn(
            e.date,
            e.kg,
            messages[e.message_id].body if e.message_id in messages else None,
            reports.get(e.date, 1),
            talks.get(e.date, []),
        )
        for e in entries
    ]


def plan_kg(weight: str | None) -> float | None:
    """The number of a plan's weight text: "ok. 60 kg" → 60, "2 x 16 kg" → 16 (per dumbbell, as
    reported weights are saved), "lekko" → None."""
    found = re.findall(r"(\d+(?:[.,]\d+)?)\s*kg", weight or "", re.IGNORECASE)
    return float(found[-1].replace(",", ".")) if found else None


@dataclass
class Dose:
    """What was done of one reported exercise: the trainee's numbers, or, when they gave none
    ("reszta wg planu"), the plan's for that exercise on that day (`planned`), from the plan
    version the training was checked against."""

    exercise: TrainingExercise
    planned: PlanExercise | None = None

    @property
    def from_plan(self) -> bool:
        return self.planned is not None

    @property
    def kg(self) -> float | None:
        return plan_kg(self.planned.weight) if self.planned else self.exercise.weight_kg


def dose_of(exercise: TrainingExercise, day: PlanDay | None) -> Dose:
    """The exercise's Dose: the plan's numbers only for a plan exercise reported without any
    (not a replacement, which has its own exercise)."""
    reported = any(v is not None for v in (exercise.sets, exercise.reps, exercise.weight_kg))
    if day is None or reported or not exercise.in_plan or exercise.replaces:
        return Dose(exercise)
    planned = next((e for e in day.exercises if e.id == exercise.exercise_id), None)
    return Dose(exercise, planned)


def plan_versions(session: Session, trainee: Trainee, checkins) -> dict[int, TrainingPlan]:
    """The plan versions the trainings were checked against (each the trainee's own)."""
    ids = {c.plan_version_id for c in checkins if c.plan_version_id}
    if not ids:
        return {}
    return {
        v.id: TrainingPlan.model_validate(v.plan)
        for v in session.scalars(
            select(TraineePlan).where(TraineePlan.trainee_id == trainee.id, TraineePlan.id.in_(ids))
        )
    }


def plan_day_of(checkin: TrainingCheckin, versions: dict[int, TrainingPlan]) -> PlanDay | None:
    """The plan day a training was checked against, from its version (None outside the plan)."""
    version = versions.get(checkin.plan_version_id)
    if version is None or not checkin.plan_day_id:
        return None
    return next((d for d in version.days if d.id == checkin.plan_day_id), None)


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
    # The conversation of each report of this kind that day, oldest first (the last one counts).
    conversations: list[list[Message]] = field(default_factory=list)
    # The plan day it was checked against (for the plan's numbers, dose()).
    day: PlanDay | None = None

    def dose(self, exercise: TrainingExercise) -> Dose:
        return dose_of(exercise, self.day)

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
    every_report = list(session.scalars(select(TrainingCheckin).where(*in_range)))
    reports = Counter((c.date, c.outside_plan) for c in every_report)
    talks = _reports(session, trainee, every_report, lambda c: (c.date, c.outside_plan))
    messages = _messages(session, checkins)
    versions = plan_versions(session, trainee, checkins)
    current_version = trainee.plan_version
    current = versions.get(current_version.id) if current_version else None
    if current_version is not None and current is None:
        current = TrainingPlan.model_validate(current_version.plan)
    current_labels = current.day_labels() if current else {}
    current_names = {d.id: d.name for d in current.days} if current else {}

    found = []
    for c in checkins:
        version = versions.get(c.plan_version_id)
        day = plan_day_of(c, versions)
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
                conversations=talks.get((c.date, c.outside_plan), []),
                day=day,
            )
        )
    return found


def last_training_date(session: Session, trainee: Trainee) -> dt.date | None:
    return session.scalar(
        select(func.max(TrainingCheckin.date)).where(TrainingCheckin.trainee_id == trainee.id)
    )


# Ćwiczenia (Treningi's second tab): every exercise done, from the reports that count.


def exercise_key(exercise: TrainingExercise) -> str:
    """What makes two reported exercises the same one: the catalog ID, or for an exercise
    without one (not in the catalog) its name, ignoring case."""
    return exercise.exercise_id or f"name:{exercise.name.strip().lower()}"


def exercise_name(exercise: TrainingExercise) -> str:
    """The catalog's name for a catalog exercise (the same however the trainee wrote it)."""
    found = exercises.BY_ID.get(exercise.exercise_id or "")
    return found.name if found else exercise.name


@dataclass
class ExerciseDone:
    """One exercise in one of the trainee's current trainings."""

    checkin: TrainingCheckin
    exercise: TrainingExercise
    day: PlanDay | None = None  # the plan day it was checked against

    @property
    def date(self) -> dt.date:
        return self.checkin.date

    @property
    def dose(self) -> Dose:
        return dose_of(self.exercise, self.day)


def exercises_done(
    session: Session, trainee: Trainee, first: dt.date | None = None, last: dt.date | None = None
) -> list[ExerciseDone]:
    """The exercises of the trainee's current trainings (the newest report of each day and kind,
    like the calendar), newest first; from `first` to `last` (inclusive) when given."""
    newest = _newest_ids(TrainingCheckin, trainee, TrainingCheckin.outside_plan)
    if first is not None:
        newest = newest.where(TrainingCheckin.date >= first)
    if last is not None:
        newest = newest.where(TrainingCheckin.date <= last)
    checkins = list(
        session.scalars(
            select(TrainingCheckin)
            .where(TrainingCheckin.id.in_(newest))
            .options(selectinload(TrainingCheckin.exercises))
            .order_by(TrainingCheckin.date.desc(), TrainingCheckin.id.desc())
        )
    )
    versions = plan_versions(session, trainee, checkins)
    return [ExerciseDone(c, e, plan_day_of(c, versions)) for c in checkins for e in c.exercises]


@dataclass
class ExerciseSummary:
    """One exercise over a time range: how often and how much."""

    key: str
    name: str
    times: int  # trainings it was in
    last: dt.date
    best_kg: float | None  # the heaviest weight (the plan's where the trainee gave none)
    latest: ExerciseDone  # the newest time it was done


def summarize(done: list[ExerciseDone]) -> list[ExerciseSummary]:
    """Per exercise, the most frequent first."""
    by_key: dict[str, list[ExerciseDone]] = {}
    for d in done:
        by_key.setdefault(exercise_key(d.exercise), []).append(d)
    found = []
    for key, rows in by_key.items():
        weights = [r.dose.kg for r in rows if r.dose.kg is not None]
        found.append(
            ExerciseSummary(
                key=key,
                name=exercise_name(rows[0].exercise),
                times=len({r.checkin.id for r in rows}),
                last=max(r.date for r in rows),
                best_kg=max(weights) if weights else None,
                latest=max(rows, key=lambda r: (r.date, r.checkin.id)),
            )
        )
    return sorted(found, key=lambda s: (-s.times, s.name.lower()))
