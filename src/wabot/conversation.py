"""Conversation flow shared by the webhook and the scripts: history, composing and sending."""

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot import assistant, utils, whatsapp
from wabot.config import Settings
from wabot.db import SessionLocal
from wabot.models import Message, Trainee

HISTORY_WINDOW = timedelta(hours=24)
HISTORY_LIMIT = 20
# WhatsApp delivers free-form messages only within 24 h of the trainee's last message.
SERVICE_WINDOW = timedelta(hours=24)
# The bot answers only messages that mention it by name, in any Polish case ("yanku", "yanka").
BOT_NAME = re.compile(r"\byan(?:ek|ka|ku|kiem|kowi)\b", re.IGNORECASE)


def mentions_bot(text: str | None) -> bool:
    return text is not None and BOT_NAME.search(text) is not None


def recent_history(session: Session, trainee_id: int, now: datetime) -> list[Message]:
    """The trainee's last messages within the history window, oldest first."""
    newest_first = session.scalars(
        select(Message)
        .where(Message.trainee_id == trainee_id, Message.created_at >= now - HISTORY_WINDOW)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(HISTORY_LIMIT)
    )
    return list(reversed(newest_first.all()))


def asked_again(session: Session, message: Message) -> bool:
    """Whether the trainee called the bot by name again after this message, with no bot message
    in between.

    The reply to that later message covers this one as well: both come after the bot's last
    message, so both are part of the report it reads. Answering both would send two replies,
    possibly out of order ("zanotowałem 80" arriving after "zanotowałem 81").
    """
    later = session.execute(
        select(Message.direction, Message.body)
        .where(Message.trainee_id == message.trainee_id, Message.id > message.id)
        .order_by(Message.id)
    )
    for direction, body in later:
        if direction == "out":
            return False
        if mentions_bot(body):
            return True
    return False


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


def compose_first_message(
    settings: Settings, trainee: Trainee, history: list[Message], now: datetime
) -> str:
    """Text for a message the bot starts itself."""
    if settings.hardcoded_first_message:
        return utils.hardcoded_first_message()
    return assistant.generate_first_message(settings, trainee, history, now)


def send_and_store(settings: Settings, trainee: Trainee, text: str) -> str:
    """Send a text message to the trainee and save it as chat history. Returns its wamid."""
    wamid = whatsapp.send_text(settings, trainee.phone, text)
    with SessionLocal() as session:
        session.add(
            Message(
                wamid=wamid,
                direction="out",
                phone=trainee.phone,
                type="text",
                body=text,
                trainee_id=trainee.id,
                trainer_id=trainee.trainer_id,
            )
        )
        session.commit()
    return wamid
