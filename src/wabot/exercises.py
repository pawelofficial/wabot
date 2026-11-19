"""The exercise catalog: fixed IDs every exercise is stored under.

The built-in exercises are in exercises.yaml; trainers add their own in the dashboard (Baza
ćwiczeń, table custom_exercises, custom_exercises.py), each visible to the trainer who added it.
Both live in this module's registry: custom_exercises.refresh() swaps in the trainers' ones (on
every request, main.py, so the bot and the dashboard process both see a new one).

Training plans are built from it (plans.py), and progress is charted by its IDs, so the same
exercise keeps one ID however the trainer or trainee names it. The whole catalog isn't sent to
the LLM; it sees only the exercises of the trainee's plan.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import AfterValidator, WithJsonSchema


@dataclass(frozen=True)
class CatalogExercise:
    id: str
    name: str
    aliases: tuple[str, ...]
    group: str
    trainer_id: int | None = None  # who added it (custom_exercises); None: built in

    def describe(self) -> str:
        """How the prompt shows it: "Suwnica na nogi (też: suwnica, leg press)"."""
        return f"{self.name} (też: {', '.join(self.aliases)})" if self.aliases else self.name


CATALOG_FILE = Path(__file__).with_name("exercises.yaml")


def load_catalog(path: Path = CATALOG_FILE) -> list[CatalogExercise]:
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    catalog = [
        CatalogExercise(e["id"], e["name"], tuple(e.get("aliases", [])), e["group"])
        for e in entries
    ]
    ids = [e.id for e in catalog]
    if len(set(ids)) != len(ids):
        raise ValueError(f"Duplicate exercise IDs in {path}")
    return catalog


# The built-in catalog (exercises.yaml).
CATALOG = load_catalog()
IDS = tuple(e.id for e in CATALOG)
GROUPS = tuple(dict.fromkeys(e.group for e in CATALOG))  # "nogi", "pośladki", ...

# Every exercise by ID, built-in and every trainer's own (names are shown wherever an ID is).
# Replaced as a whole by set_custom(), never changed in place.
BY_ID: dict[str, CatalogExercise] = {e.id: e for e in CATALOG}
CUSTOM: tuple[CatalogExercise, ...] = ()


def set_custom(custom: list[CatalogExercise]) -> None:
    """The trainers' own exercises (custom_exercises.refresh())."""
    global BY_ID, CUSTOM
    CUSTOM = tuple(custom)
    BY_ID = {e.id: e for e in CATALOG} | {e.id: e for e in custom}


def of_trainer(trainer_id: int | None) -> list[CatalogExercise]:
    """What a trainer can put in a plan: the built-in exercises and their own."""
    return [*CATALOG, *(e for e in CUSTOM if e.trainer_id == trainer_id)]


def ids_of_trainer(trainer_id: int | None) -> list[str]:
    return [e.id for e in of_trainer(trainer_id)]


def _known(exercise_id: str) -> str:
    if exercise_id not in BY_ID:
        raise ValueError(f"unknown exercise ID {exercise_id!r}")
    return exercise_id


# The placeholder json_schema() replaces with the allowed IDs (llm_output.json_schema()).
SCHEMA_PLACEHOLDER = "__exercise_ids__"

# A catalog ID (built-in or a trainer's own); as a pydantic type it rejects anything else. In the
# LLM's JSON schema it's an enum, filled in per trainer by llm_output.json_schema().
ExerciseId = Annotated[
    str,
    AfterValidator(_known),
    WithJsonSchema({"type": "string", "enum": [SCHEMA_PLACEHOLDER]}),
]


def key(name: str) -> str:
    """How names are compared: case and extra spaces don't matter."""
    return " ".join(name.casefold().split())


_BY_NAME = {key(alias): e.id for e in CATALOG for alias in (e.name, *e.aliases)}


def lookup(name: str, trainer_id: int | None = None) -> str | None:
    """The ID of the exercise with this name or alias (case-insensitive), or None: the built-in
    ones, then the trainer's own."""
    wanted = key(name)
    if wanted in _BY_NAME:
        return _BY_NAME[wanted]
    for e in CUSTOM:
        if e.trainer_id == trainer_id and wanted in {key(n) for n in (e.name, *e.aliases)}:
            return e.id
    return None
