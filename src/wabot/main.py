import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wabot import (
    assistant,
    conversation,
    entries,
    logs,
    plans,
    telegram,
    trainees,
    voice,
    whatsapp,
)
from wabot.config import Settings, get_settings
from wabot.dashboard import routes as dashboard
from wabot.db import SessionLocal, get_session
from wabot.llm_output import LlmOutput
from wabot.models import Message, Trainee, utcnow

log = logging.getLogger(__name__)
# uvicorn configures only its own loggers; this sets up the app's (LOG_LEVEL, LOG_FILE).
logs.setup(get_settings())


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Which database this process uses (DATABASE_URL), so it's never a guess.
    log.info("Database: %s", make_url(settings.database_url).render_as_string(hide_password=True))
    if missing := settings.missing_for_channel():
        log.warning(
            "CHANNEL=%s, but %s isn't set in .env: the bot can't send or receive messages",
            settings.channel,
            ", ".join(missing),
        )
    yield


app = FastAPI(title="wabot", lifespan=lifespan)
# The trainers' dashboard under /dashboard.
dashboard.install(app)

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


@app.get("/")
def health() -> dict:
    return {"status": "ok"}


@app.get("/webhook", response_class=PlainTextResponse)
def verify_webhook(
    settings: SettingsDep,
    mode: Annotated[str, Query(alias="hub.mode")],
    token: Annotated[str, Query(alias="hub.verify_token")],
    challenge: Annotated[str, Query(alias="hub.challenge")],
) -> str:
    """Meta calls this once when the webhook URL is saved in the app dashboard."""
    if (
        mode != "subscribe"
        or not settings.whatsapp_verify_token
        or token != settings.whatsapp_verify_token
    ):
        raise HTTPException(status_code=403)
    return challenge


@app.post("/webhook")
async def receive_webhook(
    request: Request, settings: SettingsDep, session: SessionDep, background: BackgroundTasks
) -> dict:
    body = await request.body()
    if not whatsapp.verify_signature(
        settings.whatsapp_app_secret, body, request.headers.get("X-Hub-Signature-256")
    ):
        log.warning("WhatsApp webhook refused: wrong signature (WHATSAPP_APP_SECRET?)")
        raise HTTPException(status_code=401)

    payload = await request.json()
    for status in whatsapp.failed_statuses(payload):
        # E.g. 131047 when a free-form message is sent outside the 24 h window.
        log.warning("WhatsApp could not deliver %s: %s", status.get("id"), status.get("errors"))

    for msg in whatsapp.parse_messages(payload):
        trainee = trainees.find_by_phone(session, msg.phone)
        stored = Message(
            channel="whatsapp",
            external_id=msg.wamid,
            direction="in",
            phone=msg.phone,
            type=msg.type,
        )
        if trainee is not None:
            stored.body = msg.text
            stored.trainee_id = trainee.id
            stored.trainer_id = trainee.trainer_id
        # Otherwise the number isn't registered: keep only what dedup needs, not the text.
        session.add(stored)
        try:
            session.commit()
        except IntegrityError:
            # Meta redelivered a message we already stored.
            session.rollback()
            log.info("WhatsApp message %s: already stored (redelivered)", msg.wamid)
            continue
        log_inbound("WhatsApp", stored, trainee, msg.text)
        # Locally these run after the 200 is sent; in production they become Cloud Tasks.
        if trainee is None:
            background.add_task(reply_unregistered, msg)
        elif conversation.mentions_bot(msg.text):
            # Only messages addressed to the bot by name get an answer; the rest is just stored
            # and is part of the history the next time the bot is asked.
            background.add_task(reply, stored.id)

    return {"status": "ok"}


@app.post("/telegram/webhook")
async def receive_telegram(
    request: Request, settings: SettingsDep, session: SessionDep, background: BackgroundTasks
) -> dict:
    """Telegram sends every message to the bot here (scripts/set_webhook.py sets it up).

    Telegram doesn't say who is behind a chat, so a new chat is asked for the phone number
    (a button that shares it); the trainee with that number is then reached in this chat
    (trainees.telegram_chat_id). After that it's the same as on WhatsApp."""
    if not telegram.verify_secret(settings, request.headers.get(telegram.SECRET_HEADER)):
        # Most likely the bot token changed since the webhook was set: restart run.sh.
        log.warning("Telegram webhook refused: wrong secret (bot token changed? restart run.sh)")
        raise HTTPException(status_code=401)
    msg = telegram.parse_update(await request.json())
    if msg is None or not msg.private:
        # Edits and the like, and groups: the bot is a one-to-one chat (docs/PLAN.md).
        log.debug("Telegram update ignored: not a message in a private chat")
        return {"status": "ok"}

    trainee = trainees.find_by_telegram(session, msg.chat_id)
    # The sender's own number, from the button (Telegram has verified it), not someone else's
    # contact card.
    own_number = msg.contact_phone is not None and msg.contact_user_id == msg.user_id
    linked = False
    if trainee is None and own_number:
        trainee = trainees.link_telegram(session, msg.contact_phone, msg.chat_id)
        linked = trainee is not None
        if linked:
            log.info(
                "Telegram chat %s is now trainee %s (%s)", msg.chat_id, trainee.id, trainee.name
            )
        else:
            log.info(
                "Telegram chat %s shared a number that isn't a trainee's (scripts/add_trainee.py)",
                msg.chat_id,
            )
    elif trainee is None and msg.contact_phone is not None:
        log.info("Telegram chat %s shared someone else's contact: not used", msg.chat_id)
    stored = Message(channel="telegram", external_id=msg.external_id, direction="in", type=msg.type)
    if trainee is not None:
        stored.phone = trainee.phone
        stored.body = msg.text
        stored.trainee_id = trainee.id
        stored.trainer_id = trainee.trainer_id
    # Otherwise it's someone who isn't a linked trainee: only what dedup needs, not the text.

    if linked:
        notice = (TELEGRAM_LINKED, telegram.REMOVE_KEYBOARD)
    elif trainee is None:
        # Asked for the number (again), or the number isn't a trainee's (the button stays, to
        # try again once the trainer has registered it).
        notice = (UNREGISTERED if own_number else TELEGRAM_ASK_FOR_PHONE, telegram.ASK_FOR_PHONE)
    elif (msg.text or "").startswith("/start"):
        # Telegram's "Start" button, e.g. after the trainee cleared the chat.
        notice = (TELEGRAM_HELP, None)
    else:
        notice = None
    text = msg.text
    if trainee is not None and notice is None and msg.voice_file_id is not None:
        # A voice message: its text is the transcript (with the caption, if it has one), and from
        # here on it's read like a typed message. Transcribed before storing, so no reply sees it
        # without its text; a redelivery isn't transcribed (paid) a second time.
        if session.scalar(select(Message.id).where(Message.external_id == msg.external_id)):
            log.info("Telegram message %s: already stored (redelivered)", msg.external_id)
            return {"status": "ok"}
        transcript = await asyncio.to_thread(
            voice.text_of, settings, msg.external_id, msg.voice_file_id, msg.voice_duration
        )
        if transcript is None:
            notice = (VOICE_NOT_UNDERSTOOD, None)
        else:
            text = " ".join(filter(None, [msg.text, transcript]))
            stored.body = text
    if trainee is not None and notice is not None:
        # The shared number, /start or an untranscribed voice message: answered by the fixed
        # text, not a report, so the next reply doesn't read it as a new message.
        stored.handled_at = utcnow()

    session.add(stored)
    try:
        session.commit()
    except IntegrityError:
        # Telegram redelivered a message we already stored (the link, if any, is already made).
        session.rollback()
        log.info("Telegram message %s: already stored (redelivered)", msg.external_id)
        return {"status": "ok"}
    log_inbound("Telegram", stored, trainee, text)

    if notice is None and conversation.calls_bot(msg.type, text):
        background.add_task(reply, stored.id)
    if notice is not None:
        trainee_id = trainee.id if trainee is not None else None
        background.add_task(send_telegram_notice, msg.chat_id, *notice, trainee_id)
    return {"status": "ok"}


def log_inbound(channel: str, stored: Message, trainee: Trainee | None, text: str | None) -> None:
    """One line per incoming message: who it's from and whether the bot answers it."""
    if trainee is None:
        log.info("%s message %s from someone who isn't a trainee", channel, stored.external_id)
        return
    if stored.handled_at is not None:
        what = ": answered with a fixed text"
    elif conversation.calls_bot(stored.type, text):
        what = ""
    else:
        what = ": not addressed to the bot, waits for the next yanek"
    log.info(
        "%s message %s (%s) from trainee %s (%s)%s",
        channel,
        stored.external_id,
        stored.type,
        trainee.id,
        trainee.name,
        what,
    )
    log.debug("Text of %s: %r", stored.external_id, text)


# The messages it answers stay waiting (Message.handled_at), so the next "yanek" reads them again.
APOLOGY = (
    "Przepraszam, mam chwilowy problem. Twoje wiadomości mam – napisz „yanek” za kilka minut, "
    "a wtedy je zapiszę."
)
UNREGISTERED = "Ten numer nie jest zarejestrowany – skontaktuj się ze swoim trenerem."
# Telegram only: the bot learns who someone is when they share their number.
TELEGRAM_ASK_FOR_PHONE = (
    "Cześć! Jestem Yanek, asystent Twojego trenera. Żeby zacząć, udostępnij mi swój numer "
    "telefonu przyciskiem poniżej – sprawdzę, czy trener Cię zarejestrował."
)
TELEGRAM_HELP = (
    "Wysyłaj mi raporty, zaczynając wiadomość od „yanek”, np. „yanek waga 80,5” albo "
    "„yanek dzień A zrobiony”."
)
TELEGRAM_LINKED = "Dzięki, numer się zgadza – możemy zaczynać! " + TELEGRAM_HELP
# A voice message that couldn't be turned into text (too long, nothing recognised, a failure).
VOICE_NOT_UNDERSTOOD = (
    "Nie udało mi się odsłuchać tej wiadomości głosowej – nagraj ją jeszcze raz (do 5 minut) "
    "albo napisz raport, zaczynając od „yanek”."
)


# How many times reply() reads the messages again when another reply took some of them while this
# one waited for the LLM (two replies running at once; conversation.claim()).
TURN_ATTEMPTS = 2


class HandledMeanwhile(Exception):
    """Another reply took some of the turn's messages while this one waited for the LLM."""


def reply(message_id: int) -> None:
    """Answer a trainee's message that mentions the bot ("yanek ..."). The reply reads the
    trainee's waiting messages as new (conversation.Turn), saves what they report and marks them
    handled in one transaction, then sends the answer back where the message came from
    (docs/CONVERSATION.md)."""
    settings = get_settings()
    for _ in range(TURN_ATTEMPTS):
        now = utcnow()
        with SessionLocal() as session:
            message = session.get_one(Message, message_id)
            if reason := conversation.not_answered_because(session, message):
                log.info("Not answering %s: %s", message.external_id, reason)
                return
            # With the trainee's current plan version: the LLM call and the save use this one.
            turn = conversation.load_turn(session, message, now)
        log.info(
            "Answering %s: trainee %s (%s), plan: %s, %d new messages after %d of context",
            message.external_id,
            turn.trainee.id,
            turn.trainee.name,
            plan_summary(turn.trainee),
            len(turn.new),
            len(turn.context),
        )
        result = ask_llm(settings, turn, now)
        with SessionLocal() as session:
            try:
                text = save_report(session, turn, result, settings.daily_updates_only)
            except HandledMeanwhile:
                log.info(
                    "Another reply took some of the messages of %s meanwhile; reading them again",
                    message.external_id,
                )
                continue
        if text is None:
            log.info(
                "Not answering %s: the trainee called the bot again, and the reply to that "
                "reads these messages too",
                message.external_id,
            )
            return
        send_reply(settings, turn, text)
        return
    log.warning("Not answering %s: other replies kept taking its messages", message_id)


def ask_llm(settings: Settings, turn: conversation.Turn, now: datetime) -> LlmOutput | None:
    """The LLM's answer to the turn, or None if the call failed (logged)."""
    try:
        return assistant.generate_reply(settings, turn.trainee, turn.context, turn.new, now)
    except assistant.MissingApiKeyError as e:
        log.error("Can't reply to %s: %s", turn.trigger.external_id, e)
    except Exception:
        log.exception("LLM call for %s failed", turn.trigger.external_id)
    return None


def send_reply(settings: Settings, turn: conversation.Turn, text: str) -> None:
    """Send the answer back where the message came from, and store it. A failed send is only
    logged: what the messages reported is saved and they're handled, so the next "yanek" doesn't
    save it again."""
    message = turn.trigger
    try:
        conversation.send_and_store(settings, turn.trainee, text, message.channel)
        log.info("Answered %s on %s", message.external_id, message.channel)
        log.debug("Answer to %s: %r", message.external_id, text)
    except httpx.HTTPStatusError as e:
        # Meta explains the problem in the response body.
        log.error("Sending reply to %s failed: %s", message.external_id, e.response.text)
    except (telegram.TelegramError, conversation.NotOnTelegramError) as e:
        log.error("Sending reply to %s failed: %s", message.external_id, e)
    except Exception:
        log.exception("Sending reply to %s failed", message.external_id)


def plan_summary(trainee: Trainee) -> str:
    """The trainee's plan for the log: "version 3, 4 days", "none". A stored plan that no longer
    passes the checks is only noted here; the LLM call below fails on it and the trainee gets the
    apology."""
    try:
        plan = plans.of(trainee)
    except ValueError:  # pydantic's ValidationError
        return f"version {trainee.plan_version_id}, not valid"
    return f"version {trainee.plan_version_id}, {len(plan.days)} days" if plan else "none"


def save_report(
    session: Session,
    turn: conversation.Turn,
    result: LlmOutput | None,
    daily_updates_only: bool = False,
) -> str | None:
    """Save what the LLM extracted and mark the turn's waiting messages handled, in one
    transaction. Returns the text to send:
    - the LLM's reply;
    - the apology if the LLM call or the save failed: nothing is saved and the messages keep
      waiting, so the next "yanek" reads them again (the reply would confirm something that
      isn't saved);
    - None if the trainee called the bot again meanwhile: the reply to that reads these messages
      too.
    Raises HandledMeanwhile if another reply took some of the messages meanwhile."""
    message = turn.trigger
    now = utcnow()
    # Marked first: on SQLite that takes the write lock, so a message arriving now is stored
    # either before the check below (which then sees it) or after this commit.
    claimed = conversation.claim(session, turn, now)
    if conversation.called_again(session, message):
        session.rollback()
        return None
    if not claimed:
        session.rollback()
        raise HandledMeanwhile
    if result is None:
        session.rollback()
        return APOLOGY
    try:
        # Commits the marks together with the entries.
        entries.save(session, turn.trainee, message, result, daily_updates_only, now=now)
    except Exception:
        log.exception("Saving the report from %s failed", message.external_id)
        session.rollback()
        return APOLOGY
    for kind, data in (("weight", result.weight), ("training", result.training)):
        if data is not None:
            log.info("Saved %s from %s: %s", kind, message.external_id, data.model_dump_json())
    return result.reply


def reply_unregistered(msg: whatsapp.InboundMessage) -> None:
    """Tell a number that isn't a registered trainee to contact their trainer. Not stored."""
    log.info(
        "Message %s is from a number that isn't registered (scripts/add_trainee.py)", msg.wamid
    )
    try:
        whatsapp.send_text(get_settings(), msg.phone, UNREGISTERED)
    except httpx.HTTPStatusError as e:
        log.error("Sending to %s failed: %s", msg.wamid, e.response.text)
    except Exception:
        log.exception("Sending to %s failed", msg.wamid)


def send_telegram_notice(
    chat_id: int, text: str, keyboard: dict | None, trainee_id: int | None = None
) -> None:
    """A fixed text to a Telegram chat (asking for the number, the welcome, the help), with a
    keyboard (telegram.ASK_FOR_PHONE / REMOVE_KEYBOARD). To a trainee (the welcome, the help)
    it's stored as chat history, so the model sees what the bot told them; to someone the bot
    doesn't know, it isn't (like UNREGISTERED on WhatsApp)."""
    try:
        external_id = telegram.send_text(get_settings(), chat_id, text, keyboard)
        log.debug("Sent to Telegram chat %s: %r", chat_id, text)
    except telegram.TelegramError as e:
        log.error("Sending to Telegram chat %s failed: %s", chat_id, e)
        return
    except Exception:
        log.exception("Sending to Telegram chat %s failed", chat_id)
        return
    if trainee_id is not None:
        with SessionLocal() as session:
            trainee = session.get_one(Trainee, trainee_id)
        conversation.store_sent(trainee, "telegram", external_id, text)
