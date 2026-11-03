"""The exercise catalog (exercises.yaml): fixed IDs every exercise is mapped to."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CatalogExercise:
    id: str
    name: str
    aliases: tuple[str, ...]
    group: str


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


def catalog_text() -> str:
    """The catalog as the prompt shows it, one exercise per line."""
    lines = []
    for e in CATALOG:
        aliases = f" (też: {', '.join(e.aliases)})" if e.aliases else ""
        lines.append(f"- {e.id}: {e.name}{aliases} [{e.group}]")
    return "\n".join(lines)
