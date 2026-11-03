"""Training plans: days of exercises from the catalog, each with the replacements the trainer
allows.

A plan is written as YAML (plans/example.yaml; later built in the dashboard from the catalog),
stored as JSON in trainees.training_plan and checked by the models below. The LLM gets the plan
as text (`prompt_text()`), with each exercise's ID, catalog name and aliases, so it can map
"siady" or "suwnica" to an ID without seeing the whole catalog.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from wabot import exercises
from wabot.exercises import ExerciseId


class _Strict(BaseModel):
    # No unknown keys (a typo like "replacement:" fails instead of being dropped); numbers are
    # fine where text is expected (reps: 8).
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)


class PlanExercise(_Strict):
    id: ExerciseId
    sets: int | None = None
    reps: str | None = None  # "8", "6–8", "10 na nogę", "45 s", "max"
    weight: str | None = None  # as the trainer writes it: "ok. 60 kg", "2 x 16 kg", "lekko"
    note: str | None = None  # "z hantlami", "z gumą"
    # Catalog IDs the trainee may do instead of this exercise.
    replacements: list[ExerciseId] = []

    @model_validator(mode="after")
    def _check_replacements(self):
        if self.id in self.replacements:
            raise ValueError(f"{self.id} is listed as its own replacement")
        if len(set(self.replacements)) != len(self.replacements):
            raise ValueError(f"{self.id} has a replacement listed twice")
        return self

    def dose(self) -> str:
        """ "4 x 6–8, ok. 60 kg" """
        reps = f"{self.sets} x {self.reps}" if self.sets and self.reps else self.reps
        if self.sets and not self.reps:
            reps = f"{self.sets} serie"
        return ", ".join(part for part in (reps, self.weight, self.note) if part)


class PlanDay(_Strict):
    name: str
    exercises: list[PlanExercise] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_exercises(self):
        ids = [e.id for e in self.exercises]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.name}: an exercise is listed twice")
        return self


class TrainingPlan(_Strict):
    days: list[PlanDay] = Field(min_length=1)
    # Everything that isn't a day of exercises: the goal, cardio days, the trainer's remarks.
    notes: str | None = None

    @model_validator(mode="after")
    def _check_days(self):
        names = [_key(d.name) for d in self.days]
        if len(set(names)) != len(names):
            raise ValueError("two days have the same name")
        return self

    def day(self, name: str) -> PlanDay | None:
        return next((d for d in self.days if _key(d.name) == _key(name)), None)

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


def to_db(plan: TrainingPlan | None) -> dict | None:
    return plan.model_dump(mode="json", exclude_defaults=True) if plan else None


def of(trainee) -> TrainingPlan | None:
    """The trainee's plan, from the JSON stored with them."""
    return TrainingPlan.model_validate(trainee.training_plan) if trainee.training_plan else None
