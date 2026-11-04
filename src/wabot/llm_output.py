"""What the LLM returns, and the checks it must pass before the app uses it.

The pydantic models are the single source of the structure: the JSON schema sent to OpenAI is
generated from them (`json_schema()`), and `validate_output()` parses the response into them and
then checks the values – plausible ranges, dates, and exercise IDs that must come from the
training plan. What the code can work out itself it fills in after the checks: which plan
exercises were missed, whether the day was done or partial, and catalog IDs by name.
The meaning of each field is explained to the model in prompts.yaml.
"""

import datetime as dt
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.json_schema import SkipJsonSchema

from wabot import exercises
from wabot.exercises import ExerciseId
from wabot.plans import TrainingPlan

MIN_KG, MAX_KG = 30, 300
MAX_SETS = 20
MAX_LOAD_KG = 500
# Reports about older days are probably a misread date.
MAX_REPORT_AGE = dt.timedelta(days=31)
# WhatsApp's limit for a text message.
MAX_REPLY_CHARS = 4096


class _Strict(BaseModel):
    # No extra fields; becomes "additionalProperties": false in the schema.
    model_config = ConfigDict(extra="forbid")


class Weight(_Strict):
    kg: float
    date: dt.date


class Exercise(_Strict):
    # A catalog ID (exercises.yaml) from the trainee's plan: a planned exercise or one of its
    # replacements. The model sets null for anything else; validate_output() then looks the
    # name up in the catalog, and it stays null outside the catalog.
    exercise_id: ExerciseId | None
    name: str
    in_plan: bool
    sets: int | None
    reps: str | None
    weight_kg: float | None
    # ID of the plan exercise this one was done instead of ("suwnica zamiast przysiadów").
    replaces: ExerciseId | None
    # Anything else about this exercise, in the trainee's words.
    comment: str | None


class Training(_Strict):
    date: dt.date
    plan_day: str | None
    # For a plan day the code sets done/partial from the exercises (_fill_in_missed).
    status: Literal["done", "partial", "skipped"]
    exercises: list[Exercise]
    notes: str | None
    # IDs of the plan day's exercises that aren't in the report (done or replaced): what the
    # trainee didn't report counts as not done. Filled in by the code, so it isn't in the
    # schema the model gets.
    missed_exercises: SkipJsonSchema[list[ExerciseId]] = Field(default_factory=list)


class LlmOutput(_Strict):
    reply: str
    weight: Weight | None
    training: Training | None


class InvalidOutputError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def json_schema() -> dict:
    """The schema for OpenAI's strict structured outputs."""
    return LlmOutput.model_json_schema()


def _foreign_letters(text: str) -> str:
    """Letters that aren't Latin script (Polish letters are Latin; emoji and digits aren't
    letters). The model occasionally slips a word from another language into the reply."""
    return "".join(
        sorted(
            {
                c
                for c in text
                if unicodedata.category(c).startswith("L")
                and not unicodedata.name(c, "").startswith("LATIN")
            }
        )
    )


# Markdown bold (**x**) shows up literally on WhatsApp, which uses *x*.
_MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _to_whatsapp_format(text: str) -> str:
    return _MARKDOWN_BOLD.sub(r"*\1*", text.strip())


def _check_date(errors: list[str], field: str, value: dt.date, today: dt.date) -> None:
    if value > today:
        errors.append(f"{field} {value} is in the future (today is {today})")
    elif value < today - MAX_REPORT_AGE:
        errors.append(f"{field} {value} is more than {MAX_REPORT_AGE.days} days ago")


def _check_against_plan(errors: list[str], training: Training, plan: TrainingPlan) -> None:
    """IDs that claim to come from the plan must really be in it."""
    day = None
    if training.plan_day is not None:
        day = plan.day(training.plan_day)
        if day is None:
            names = ", ".join(repr(d.name) for d in plan.days)
            errors.append(
                f"training.plan_day {training.plan_day!r} is not a day of the plan ({names})"
            )
        else:
            training.plan_day = day.name
    planned = plan.planned_ids(day)
    where_planned = f"in {day.name!r}" if day else "in the plan"

    for ex in training.exercises:
        where = f"exercise {ex.name!r}"
        if ex.in_plan:
            if ex.exercise_id is None:
                errors.append(f"{where}: in_plan is true, so exercise_id must be the plan's ID")
            elif ex.exercise_id not in planned:
                errors.append(
                    f"{where}: in_plan is true but {ex.exercise_id} isn't {where_planned}"
                )
        elif ex.exercise_id in planned and ex.replaces is None:
            errors.append(f"{where}: {ex.exercise_id} is {where_planned}, so in_plan must be true")
        if ex.replaces is not None and ex.replaces not in planned:
            errors.append(f"{where}: replaces {ex.replaces}, which isn't {where_planned}")


def validate_output(raw: str, today: dt.date, plan: TrainingPlan | None) -> LlmOutput:
    """Parse the model's JSON and check it; raises InvalidOutputError listing every problem."""
    try:
        output = LlmOutput.model_validate_json(raw)
    except ValidationError as e:
        raise InvalidOutputError(
            [f"{'.'.join(map(str, err['loc'])) or 'output'}: {err['msg']}" for err in e.errors()]
        ) from e

    errors: list[str] = []
    if not output.reply.strip():
        errors.append("reply is empty")
    elif output.reply.lstrip().startswith("{"):
        errors.append("reply must be a WhatsApp message, not JSON")
    if len(output.reply) > MAX_REPLY_CHARS:
        errors.append(
            f"reply has {len(output.reply)} characters, WhatsApp allows {MAX_REPLY_CHARS}"
        )
    if foreign := _foreign_letters(output.reply):
        errors.append(f"reply contains non-Polish letters {foreign!r}; write only in Polish")

    if (weight := output.weight) is not None:
        if not MIN_KG <= weight.kg <= MAX_KG:
            errors.append(f"weight.kg {weight.kg} is outside {MIN_KG}–{MAX_KG} kg")
        _check_date(errors, "weight.date", weight.date, today)

    if (training := output.training) is not None:
        _check_date(errors, "training.date", training.date, today)
        if training.status == "skipped" and training.exercises:
            errors.append("training.status is skipped but exercises are listed")
        if training.status != "skipped" and not training.exercises:
            errors.append(f"training.status is {training.status} but no exercises are listed")

        for i, ex in enumerate(training.exercises):
            where = f"training.exercises[{i}] ({ex.name})"
            if not ex.name.strip():
                errors.append(f"training.exercises[{i}].name is empty")
            if ex.sets is not None and not 1 <= ex.sets <= MAX_SETS:
                errors.append(f"{where}: sets {ex.sets} is outside 1–{MAX_SETS}")
            if ex.weight_kg is not None and not 0 < ex.weight_kg <= MAX_LOAD_KG:
                errors.append(f"{where}: weight_kg {ex.weight_kg} is outside 0–{MAX_LOAD_KG}")
            if ex.replaces is not None and ex.in_plan:
                errors.append(f"{where}: replaces a plan exercise, so in_plan must be false")

        if plan:
            _check_against_plan(errors, training, plan)
        else:
            if training.plan_day is not None:
                errors.append("training.plan_day must be null: the trainee has no plan")
            if any(ex.in_plan for ex in training.exercises):
                errors.append("in_plan must be false: the trainee has no plan")
            if any(ex.replaces is not None for ex in training.exercises):
                errors.append("replaces must be null: the trainee has no plan")

    if errors:
        raise InvalidOutputError(errors)
    # Slips the code can fix itself, without another LLM call.
    output.reply = _to_whatsapp_format(output.reply)
    if output.training is not None:
        _fill_in_missed(output.training, plan)
        _match_to_catalog(output.training, plan)
    return output


def _fill_in_missed(training: Training, plan: TrainingPlan | None) -> None:
    """Missed exercises and done/partial, from the plan day and the report. An exercise of the
    day that the trainee didn't report as done or replaced counts as not done."""
    day = plan.day(training.plan_day) if plan and training.plan_day else None
    if day is None:
        # Training outside the plan (or no plan): nothing to miss.
        training.missed_exercises = []
        return
    covered = {ex.exercise_id for ex in training.exercises if ex.in_plan}
    covered |= {ex.replaces for ex in training.exercises if ex.replaces}
    training.missed_exercises = [e.id for e in day.exercises if e.id not in covered]
    if training.status != "skipped":
        training.status = "partial" if training.missed_exercises else "done"


def _match_to_catalog(training: Training, plan: TrainingPlan | None) -> None:
    """Catalog IDs for exercises outside the plan. The model sees only the plan's exercises, so
    the code matches the others by name and alias ("pompki" → push_up); an ID the model picked
    from the schema's list is the fallback, and null stays null outside the catalog."""
    from_plan = plan.allowed_ids() if plan else set()
    for ex in training.exercises:
        if ex.exercise_id not in from_plan:
            ex.exercise_id = exercises.lookup(ex.name) or ex.exercise_id
