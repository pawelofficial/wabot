"""Exercises trainers add themselves (table custom_exercises), next to the built-in catalog.

refresh() loads them all into exercises' registry; main.py's middleware calls it on every request,
so the bot (another process locally) knows an exercise the dashboard just added. check() and add()
are the dashboard's "Dodaj ćwiczenie" (dashboard/exercise_pages.py).
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot import exercises
from wabot.models import CustomExercise, Trainer

log = logging.getLogger(__name__)

NAME_MAX = 100  # custom_exercises.name
ALIASES_MAX = 10
PREFIX = "custom_"
ID_MAX = 64  # custom_exercises.exercise_id


def refresh(session: Session) -> None:
    """Put every trainer's own exercises into the registry (exercises.set_custom())."""
    rows = session.scalars(select(CustomExercise).order_by(CustomExercise.id))
    exercises.set_custom(
        [
            exercises.CatalogExercise(
                r.exercise_id, r.name, tuple(r.aliases), r.group, r.trainer_id
            )
            for r in rows
        ]
    )


@dataclass
class NewExercise:
    name: str = ""
    group: str = ""
    aliases: str = ""  # comma-separated, as typed
    errors: dict[str, str] = field(default_factory=dict)

    def alias_list(self) -> list[str]:
        """The other names, tidied: no empty ones, repeats or the name itself."""
        seen, found = {exercises.key(self.name)}, []
        for alias in self.aliases.split(","):
            alias = " ".join(alias.split())
            if alias and exercises.key(alias) not in seen:
                seen.add(exercises.key(alias))
                found.append(alias)
        return found


def _taken(name: str, trainer_id: int) -> str | None:
    """The name of the exercise (built-in or the trainer's own) this name or alias already
    means, or None."""
    found = exercises.lookup(name, trainer_id)
    return exercises.BY_ID[found].name if found else None


def check(session: Session, trainer: Trainer, form: NewExercise) -> NewExercise:
    """Fill in form.errors (by field, in Polish)."""
    refresh(session)
    form.name = " ".join(form.name.split())
    if not form.name:
        form.errors["name"] = "Podaj nazwę ćwiczenia."
    elif len(form.name) > NAME_MAX:
        form.errors["name"] = f"Najwyżej {NAME_MAX} znaków."
    elif taken := _taken(form.name, trainer.id):
        form.errors["name"] = f"To ćwiczenie już jest w bazie: {taken}."
    if form.group not in exercises.GROUPS:
        form.errors["group"] = "Wybierz partię mięśni."
    aliases = form.alias_list()
    if len(aliases) > ALIASES_MAX:
        form.errors["aliases"] = f"Najwyżej {ALIASES_MAX} innych nazw."
    elif any(len(a) > NAME_MAX for a in aliases):
        form.errors["aliases"] = f"Każda nazwa najwyżej {NAME_MAX} znaków."
    else:
        clashes = [f"„{a}” to już {t}" for a in aliases if (t := _taken(a, trainer.id))]
        if clashes:
            form.errors["aliases"] = "Te nazwy są już zajęte: " + "; ".join(clashes) + "."
    return form


def _slug(name: str) -> str:
    """ "Wznosy hantli bokiem" → "wznosy_hantli_bokiem" (ASCII, for the ID)."""
    text = unicodedata.normalize("NFKD", name.replace("ł", "l").replace("Ł", "L"))
    text = text.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "cwiczenie"


def new_id(session: Session, name: str) -> str:
    """A free, permanent ID: custom_ + the name's slug (+ _2, _3 when taken)."""
    base = (PREFIX + _slug(name))[: ID_MAX - 4].rstrip("_")
    taken = set(session.scalars(select(CustomExercise.exercise_id)))
    candidate, n = base, 1
    while candidate in taken or candidate in exercises.IDS:
        n += 1
        candidate = f"{base}_{n}"
    return candidate


def add(session: Session, trainer: Trainer, form: NewExercise) -> CustomExercise:
    """Save a checked form (check() found no errors) and put it in the registry."""
    row = CustomExercise(
        trainer_id=trainer.id,
        exercise_id=new_id(session, form.name),
        name=form.name,
        aliases=form.alias_list(),
        group=form.group,
    )
    session.add(row)
    session.commit()
    refresh(session)
    log.info("Trainer %s added exercise %s (%s)", trainer.id, row.exercise_id, row.name)
    return row
