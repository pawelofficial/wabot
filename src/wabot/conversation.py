"""Conversation flow shared by the webhooks and the scripts: history, which messages a reply reads
(Turn: the ones still waiting are new, Message.handled_at; docs/CONVERSATION.md), composing and
sending (on Telegram or WhatsApp; the rest of the app doesn't know which)."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
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


def calls_bot(type_: str, text: str | None) -> bool:
    """Whether a trainee's message asks the bot for an answer: it mentions the bot by name, or
    it's a voice message (sent to the bot only; nobody says "yanek" into it)."""
    return type_ == "voice" or mentions_bot(text)


def recent_history(session: Session, trainee_id: int, now: datetime) -> list[Message]:
    """The trainee's last messages within the history window, oldest first."""
    newest_first = session.scalars(
        select(Message)
        .where(Message.trainee_id == trainee_id, Message.created_at >= now - HISTORY_WINDOW)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(HISTORY_LIMIT)
    )
    return list(reversed(newest_first.all()))


def waiting(message: Message) -> bool:
    """A trainee's message the bot hasn't dealt with yet (Message.handled_at)."""
    return message.direction == "in" and message.handled_at is None


@dataclass
class Turn:
    """One answer of the bot: the message that called it ("yanek ..."), the trainee, and their
    recent history split in two. `new` starts at the first message still waiting: what the reply
    reads reports from, together with the bot's messages in between (an apology, the help after
    /start). `context` is everything before it: already handled, there for what the new messages
    refer to."""

    trigger: Message
    trainee: Trainee
    context: list[Message]
    new: list[Message]

    @property
    def waiting_ids(self) -> list[int]:
        """The messages this reply deals with: once it has saved what they report, they're
        handled (claim())."""
        return [m.id for m in self.new if waiting(m)]


def load_turn(session: Session, trigger: Message, now: datetime) -> Turn:
    """The turn that answers `trigger`, from the trainee's recent history."""
    trainee = session.get_one(Trainee, trigger.trainee_id)
    history = recent_history(session, trainee.id, now)
    first_new = next((i for i, m in enumerate(history) if waiting(m)), len(history))
    return Turn(trigger, trainee, history[:first_new], history[first_new:])


def called_again(session: Session, message: Message) -> bool:
    """Whether the trainee called the bot by name again after this message. The reply to that
    later message reads this one too (it's still waiting), so this one gets no reply of its own:
    answering both would send two replies, possibly out of order ("zanotowałem 80" arriving
    after "zanotowałem 81")."""
    later = session.execute(
        select(Message.type, Message.body).where(
            Message.trainee_id == message.trainee_id,
            Message.direction == "in",
            Message.handled_at.is_(None),
            Message.id > message.id,
        )
    )
    return any(calls_bot(type_, body) for type_, body in later)


def not_answered_because(session: Session, message: Message) -> str | None:
    """Why `message` gets no reply of its own, or None if it gets one."""
    if message.handled_at is not None:
        return "another reply has read it already"
    if called_again(session, message):
        return "the trainee called the bot again, and the reply to that reads this one too"
    return None


def claim(session: Session, turn: Turn, now: datetime) -> bool:
    """Mark the turn's waiting messages handled, in the caller's transaction (it commits them
    together with what the reply saves, or rolls both back). False if another reply has taken
    some of them meanwhile: then this turn is out of date."""
    ids = turn.waiting_ids
    result = session.execute(
        update(Message)
        .where(Message.id.in_(ids), Message.handled_at.is_(None))
        .values(handled_at=now)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == len(ids)


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
