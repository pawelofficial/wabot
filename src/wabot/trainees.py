"""Trainees and trainers: who is behind a phone number or a Telegram chat, and registering
trainees until the dashboard does it (scripts/add_trainee.py)."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot.models import Trainee, Trainer


def normalize_phone(text: str) -> str:
    """A number as trainees.phone stores it: digits only, with the country code, the form
    WhatsApp sends (a number shared on Telegram may start with "+").

    "+48 123 456 789" → "48123456789".
    """
    digits = re.sub(r"[\s()+-]", "", text)
    if not re.fullmatch(r"\d{8,15}", digits):
        raise ValueError(f"{text!r} is not a phone number with a country code, e.g. 48123456789")
    return digits


def normalize_email(text: str) -> str:
    """Emails are stored and compared lowercase: Google may send "Jan.Kowalski@Gmail.com"."""
    email = text.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError(f"{text!r} is not an email address")
    return email


def trainer_by_email(session: Session, email: str) -> Trainer | None:
    """The trainer who signs in with this Google account, if any."""
    return session.scalar(select(Trainer).where(Trainer.email == normalize_email(email)))


def find_by_phone(session: Session, phone: str) -> Trainee | None:
    return session.scalar(select(Trainee).where(Trainee.phone == phone))


def find_by_telegram(session: Session, chat_id: int) -> Trainee | None:
    return session.scalar(select(Trainee).where(Trainee.telegram_chat_id == chat_id))


def link_telegram(session: Session, phone: str, chat_id: int) -> Trainee | None:
    """The trainee registered with this number, now reached in this Telegram chat; None if no
    trainee has the number. Only for the sender's own number (main.receive_telegram() checks):
    Telegram has verified it. A trainee who writes from another Telegram account and shares
    the number again moves to that chat."""
    try:
        trainee = find_by_phone(session, normalize_phone(phone))
    except ValueError:
        return None
    if trainee is not None:
        trainee.telegram_chat_id = chat_id
    return trainee


def trainer_named(session: Session, name: str) -> Trainer:
    """The trainer with this name, created if there is none."""
    trainer = session.scalar(select(Trainer).where(Trainer.name == name))
    if trainer is None:
        trainer = Trainer(name=name)
        session.add(trainer)
    return trainer
