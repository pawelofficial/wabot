"""Conversation flow shared by the webhooks and the scripts: history, composing and sending (on
Telegram or WhatsApp; the rest of the app doesn't know which)."""

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot import assistant, telegram, utils, whatsapp
from wabot.config import Settings
from wabot.db import SessionLocal
from wabot.models import Message, Trainee

HISTORY_WINDOW = timedelta(hours=24)
HISTORY_LIMIT = 20
# WhatsApp delivers free-form messages only within 24 h of the trainee's last message there.
# Telegram has no such window.
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
    """Whether the trainee wrote to the bot on WhatsApp recently enough for free-form messages
    there."""
    last_inbound = session.scalar(
        select(Message.id)
        .where(
            Message.phone == phone,
            Message.channel == "whatsapp",
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


class NotOnTelegramError(Exception):
    """The trainee hasn't connected to the Telegram bot yet, so the bot can't write to them."""


def send_text(settings: Settings, channel: str, trainee: Trainee, text: str) -> str:
    """Send a text message to the trainee on `channel`. Returns its external ID."""
    if channel == "telegram":
        if trainee.telegram_chat_id is None:
            raise NotOnTelegramError(
                f"{trainee.name} ({trainee.phone}) hasn't shared their number with the Telegram "
                "bot yet: they open it and press „Udostępnij numer telefonu”"
            )
        # The bot's texts use the chat formatting the prompt asks for (*bold*, _italic_).
        return telegram.send_text(settings, trainee.telegram_chat_id, text, markdown=True)
    return whatsapp.send_text(settings, trainee.phone, text)


def send_and_store(
    settings: Settings, trainee: Trainee, text: str, channel: str | None = None
) -> str:
    """Send a text message to the trainee and save it as chat history. Returns its external
    ID. `channel`: where to send it; a reply goes where the message it answers came from,
    a message the bot starts itself to CHANNEL (the default)."""
    channel = channel or settings.channel
    external_id = send_text(settings, channel, trainee, text)
    store_sent(trainee, channel, external_id, text)
    return external_id


def store_sent(trainee: Trainee, channel: str, external_id: str, text: str) -> None:
    """Save a text the bot sent to the trainee as chat history."""
    with SessionLocal() as session:
        session.add(
            Message(
                channel=channel,
                external_id=external_id,
                direction="out",
                phone=trainee.phone,
                type="text",
                body=text,
                trainee_id=trainee.id,
                trainer_id=trainee.trainer_id,
            )
        )
        session.commit()
