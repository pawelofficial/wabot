"""Training plans: days of exercises from the catalog, each with the replacements the trainer
allows.

A plan is written as YAML (plans/example.yaml, for scripts/add_trainee.py) or edited in the
dashboard (dashboard/plan_editor.py), stored as JSON in a version of the trainee's plan
(trainee_plans, plan_history.py; the trainee's row points to the current one) and checked by
the models below. The LLM gets the plan as text (`prompt_text()`), with each exercise's ID,
catalog name and aliases, so it can map "siady" or "suwnica" to an ID without seeing the whole
catalog.

Every stored plan day has an ID (`PlanDay.id`) that the trainer never sees and that stays the
same when the day is renamed or moved, in later versions of the trainee's plan and in copies of
a library plan. Saved trainings point to their day by it (training_checkins.plan_day_id), so the
calendar knows that "Dzień A" before a rename and "Poniedziałek – nogi" after it are one day.
`with_day_ids()` gives the IDs; `to_db()` makes sure no plan is stored without them.
"""

import secrets
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from wabot import exercises
from wabot.exercises import ExerciseId


class _Strict(BaseModel):
    # No unknown keys (a typo like "replacement:" fails instead of being dropped); numbers are
    # fine where text is expected (reps: 8).
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)


# The errors below have their own types (pydantic's error "type"), so the dashboard's plan editor
# can say them in Polish (dashboard/plan_editor.py).
def _error(kind: str, message: str, **context) -> PydanticCustomError:
    return PydanticCustomError(kind, message, context)


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# A plan day's ID: made by new_day_id() (8 hex digits); a YAML file may set its own.
DayId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")]


class PlanExercise(_Strict):
    id: ExerciseId
    sets: int | None = Field(None, ge=1)
    reps: str | None = None  # "8", "6–8", "10 na nogę", "45 s", "max"
    weight: str | None = None  # as the trainer writes it: "ok. 60 kg", "2 x 16 kg", "lekko"
    note: str | None = None  # "z hantlami", "z gumą"
    # Catalog IDs the trainee may do instead of this exercise.
    replacements: list[ExerciseId] = []

    @model_validator(mode="after")
    def _check_replacements(self):
        if self.id in self.replacements:
            raise _error("own_replacement", "{id} is listed as its own replacement", id=self.id)
        if len(set(self.replacements)) != len(self.replacements):
            raise _error("duplicate_replacement", "{id} has a replacement listed twice", id=self.id)
        return self

    def dose(self) -> str:
        """ "4 x 6–8, ok. 60 kg" """
        reps = f"{self.sets} x {self.reps}" if self.sets and self.reps else self.reps
        if self.sets and not self.reps:
            reps = f"{self.sets} serie"
        return ", ".join(part for part in (reps, self.weight, self.note) if part)


class PlanDay(_Strict):
    # Null only before the plan is stored (a YAML file, a day just added in the editor).
    id: DayId | None = None
    name: Name
    exercises: list[PlanExercise] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_exercises(self):
        ids = [e.id for e in self.exercises]
        twice = next((i for i in ids if ids.count(i) > 1), None)
        if twice:
            raise _error(
                "duplicate_exercise",
                "{day}: {id} is listed twice",
                day=self.name,
                id=twice,
            )
        return self


class TrainingPlan(_Strict):
    days: list[PlanDay] = Field(min_length=1)
    # Everything that isn't a day of exercises: the goal, cardio days, the trainer's remarks.
    notes: str | None = None

    @model_validator(mode="after")
    def _check_days(self):
        names = [_key(d.name) for d in self.days]
        twice = next((d.name for d in self.days if names.count(_key(d.name)) > 1), None)
        if twice:
            raise _error("duplicate_day", "two days have the same name: {name}", name=twice)
        ids = [d.id for d in self.days if d.id is not None]
        if len(set(ids)) != len(ids):
            raise _error("duplicate_day_id", "two days have the same ID")
        return self

    def day(self, name: str) -> PlanDay | None:
        return next((d for d in self.days if _key(d.name) == _key(name)), None)

    def without_day_ids(self) -> "TrainingPlan":
        """The same plan with no day IDs: for comparing what the plan says, e.g. with a YAML
        file."""
        days = [d.model_copy(update={"id": None}) for d in self.days]
        return self.model_copy(update={"days": days})

    def planned_ids(self, day: PlanDay | None = None) -> set[str]:
        """IDs of the exercises planned for the day (every day without one)."""
        return {e.id for d in ([day] if day else self.days) for e in d.exercises}

    def allowed_ids(self) -> set[str]:
        """Every ID the plan mentions: planned exercises and their replacements."""
        return self.planned_ids() | {
            r for d in self.days for e in d.exercises for r in e.replacements
        }

    def prompt_text(self) -> str:
        lines = []
        if self.notes:
            lines += [self.notes.strip(), ""]
        for day in self.days:
            lines.append(day.name)
            for e in day.exercises:
                catalog = exercises.BY_ID[e.id]
                dose = e.dose()
                lines.append(f"- {e.id}: {catalog.describe()}{f' – {dose}' if dose else ''}")
                if e.replacements:
                    alternatives = "; ".join(
                        f"{r}: {exercises.BY_ID[r].describe()}" for r in e.replacements
                    )
                    lines.append(f"  zamienniki: {alternatives}")
            lines.append("")
        return "\n".join(lines).strip()


def _key(name: str) -> str:
    return " ".join(name.casefold().split())


def load(path: Path) -> TrainingPlan:
    """Read and check a plan file; raises pydantic's ValidationError on a bad plan."""
    return TrainingPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def new_day_id(taken: set[str] = frozenset()) -> str:
    while (day_id := secrets.token_hex(4)) in taken:
        pass
    return day_id


def with_day_ids(plan: TrainingPlan, earlier: Iterable[TrainingPlan] = ()) -> TrainingPlan:
    """The plan with an ID on every day. A day that has one keeps it (the editor sends each
    day's ID back, so renaming or moving a day keeps it). A day without one takes the ID of the
    day with the same name (any case and spacing) in the first of `earlier` that has one: the
    trainee's earlier plans, newest first, so a YAML plan registered again, or a day deleted and
    added again under its name, is still the same day. Otherwise it gets a new ID, never one an
    earlier day had."""
    earlier = list(earlier)
    taken = {d.id for d in plan.days if d.id is not None}
    seen = taken | {d.id for p in earlier for d in p.days if d.id is not None}
    days = []
    for day in plan.days:
        if day.id is None:
            same_name = (
                d.id
                for p in earlier
                for d in p.days
                if d.id is not None and d.id not in taken and _key(d.name) == _key(day.name)
            )
            day_id = next(same_name, None) or new_day_id(seen)
            taken.add(day_id)
            seen.add(day_id)
            day = day.model_copy(update={"id": day_id})
        days.append(day)
    return plan.model_copy(update={"days": days})


def to_db(plan: TrainingPlan | None) -> dict | None:
    """The plan as stored (JSON). A day still without an ID gets a new one; to keep a day's
    ID from an earlier plan, pass the plan through with_day_ids() first."""
    if plan is None:
        return None
    return with_day_ids(plan).model_dump(mode="json", exclude_defaults=True)


def of(trainee) -> TrainingPlan | None:
    """The trainee's current plan: the version trainees.plan_version_id points to (loaded with
    the trainee), or None without a plan."""
    version = trainee.plan_version
    return TrainingPlan.model_validate(version.plan) if version is not None else None
