"""Trainees and trainers: who is behind a phone number, and registering trainees until the
dashboard does it (scripts/add_trainee.py)."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot.models import Trainee, Trainer


def normalize_phone(text: str) -> str:
    """WhatsApp's form of a number: digits only, with the country code.

    "+48 123 456 789" → "48123456789".
    """
    digits = re.sub(r"[\s()+-]", "", text)
    if not re.fullmatch(r"\d{8,15}", digits):
        raise ValueError(f"{text!r} is not a phone number with a country code, e.g. 48123456789")
    return digits


def find_by_phone(session: Session, phone: str) -> Trainee | None:
    return session.scalar(select(Trainee).where(Trainee.phone == phone))


def trainer_named(session: Session, name: str) -> Trainer:
    """The trainer with this name, created if there is none."""
    trainer = session.scalar(select(Trainer).where(Trainer.name == name))
    if trainer is None:
        trainer = Trainer(name=name)
        session.add(trainer)
    return trainer
