"""What the LLM returns, and the checks it must pass before the app uses it.

The pydantic models are the single source of the structure: the JSON schema sent to OpenAI is
generated from them (`json_schema()`), and `validate_output()` parses the response into them and
then checks the values – plausible ranges, dates, and names that must come from the training plan.
The meaning of each field is explained to the model in prompts.yaml.
"""

import datetime as dt
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from wabot import exercises

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


# One of the catalog IDs (exercises.yaml); the schema lists them, so the model can't invent one.
ExerciseId = Literal[exercises.IDS]


class Exercise(_Strict):
    # null when the exercise isn't in the catalog.
    exercise_id: ExerciseId | None
    name: str
    in_plan: bool
    sets: int | None
    reps: str | None
    weight_kg: float | None
    # The plan exercise this one was done instead of ("suwnica zamiast przysiadów").
    replaces: str | None
    # Anything else about this exercise, in the trainee's words.
    comment: str | None


class Training(_Strict):
    date: dt.date
    plan_day: str | None
    status: Literal["done", "partial", "skipped"]
    exercises: list[Exercise]
    missed_exercises: list[str]
    notes: str | None


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


def _in_plan(name: str, plan: str) -> bool:
    return name.strip().casefold() in plan.casefold()


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


def validate_output(raw: str, today: dt.date, training_plan: str | None) -> LlmOutput:
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
            if ex.replaces is not None:
                if ex.in_plan:
                    errors.append(f"{where}: replaces a plan exercise, so in_plan must be false")
                if ex.replaces in training.missed_exercises:
                    errors.append(
                        f"{where}: {ex.replaces!r} was replaced, so it isn't a missed exercise"
                    )

        # Names that claim to come from the plan must really be in it.
        if training_plan:
            if training.plan_day is not None and not _in_plan(training.plan_day, training_plan):
                errors.append(f"training.plan_day {training.plan_day!r} is not in the plan")
            for ex in training.exercises:
                if ex.in_plan and not _in_plan(ex.name, training_plan):
                    errors.append(f"exercise {ex.name!r} has in_plan true but is not in the plan")
            for name in training.missed_exercises:
                if not _in_plan(name, training_plan):
                    errors.append(f"missed exercise {name!r} is not in the plan")
            for ex in training.exercises:
                if ex.replaces is not None and not _in_plan(ex.replaces, training_plan):
                    errors.append(f"exercise {ex.name!r} replaces {ex.replaces!r}, not in the plan")
        else:
            if training.plan_day is not None:
                errors.append("training.plan_day must be null: the trainee has no plan")
            if any(ex.in_plan for ex in training.exercises):
                errors.append("in_plan must be false: the trainee has no plan")
            if training.missed_exercises:
                errors.append("missed_exercises must be empty: the trainee has no plan")
            if any(ex.replaces is not None for ex in training.exercises):
                errors.append("replaces must be null: the trainee has no plan")

    if errors:
        raise InvalidOutputError(errors)
    # A formatting slip the code can fix itself, without another LLM call.
    output.reply = _to_whatsapp_format(output.reply)
    return output
