"""Conversation flow shared by the webhook and the scripts: history, composing and sending."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot import assistant, utils, whatsapp
from wabot.config import Settings
from wabot.db import SessionLocal
from wabot.models import Message

HISTORY_WINDOW = timedelta(hours=24)
HISTORY_LIMIT = 20
# WhatsApp delivers free-form messages only within 24 h of the trainee's last message.
SERVICE_WINDOW = timedelta(hours=24)


def recent_history(session: Session, phone: str, now: datetime) -> list[Message]:
    """The trainee's last messages within the history window, oldest first."""
    newest_first = session.scalars(
        select(Message)
        .where(Message.phone == phone, Message.created_at >= now - HISTORY_WINDOW)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(HISTORY_LIMIT)
    )
    return list(reversed(newest_first.all()))


def in_service_window(session: Session, phone: str, now: datetime) -> bool:
    """Whether the trainee wrote to the bot recently enough for free-form messages."""
    last_inbound = session.scalar(
        select(Message.id)
        .where(
            Message.phone == phone,
            Message.direction == "in",
            Message.created_at >= now - SERVICE_WINDOW,
        )
        .limit(1)
    )
    return last_inbound is not None


def compose_first_message(settings: Settings, history: list[Message], now: datetime) -> str:
    """Text for a message the bot starts itself."""
    if settings.hardcoded_first_message:
        return utils.hardcoded_first_message()
    return assistant.generate_first_message(settings, history, now)


def send_and_store(settings: Settings, phone: str, text: str) -> str:
    """Send a text message and save it as chat history. Returns its wamid."""
    wamid = whatsapp.send_text(settings, phone, text)
    with SessionLocal() as session:
        session.add(Message(wamid=wamid, direction="out", phone=phone, type="text", body=text))
        session.commit()
    return wamid
