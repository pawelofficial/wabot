"""The exercise catalog (exercises.yaml): fixed IDs every exercise is stored under.

Training plans are built from it (plans.py), and progress is charted by its IDs, so the same
exercise keeps one ID however the trainer or trainee names it. The whole catalog isn't sent to
the LLM; it sees only the exercises of the trainee's plan.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml


@dataclass(frozen=True)
class CatalogExercise:
    id: str
    name: str
    aliases: tuple[str, ...]
    group: str

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


CATALOG = load_catalog()
IDS = tuple(e.id for e in CATALOG)
BY_ID = {e.id: e for e in CATALOG}

# One of the catalog IDs; as a pydantic type it rejects anything else, and in the LLM's JSON
# schema it lists the allowed values.
ExerciseId = Literal[IDS]


def _key(name: str) -> str:
    return " ".join(name.casefold().split())


_BY_NAME = {_key(alias): e.id for e in CATALOG for alias in (e.name, *e.aliases)}


def lookup(name: str) -> str | None:
    """The ID of the exercise with this name or alias (case-insensitive), or None."""
    return _BY_NAME.get(_key(name))
