import logging
from typing import Annotated

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wabot import assistant, conversation, entries, trainees, whatsapp
from wabot.config import Settings, get_settings
from wabot.dashboard import routes as dashboard
from wabot.db import SessionLocal, get_session
from wabot.llm_output import LlmOutput
from wabot.models import Message, Trainee, utcnow

log = logging.getLogger(__name__)
# uvicorn configures only its own loggers; this makes the app's INFO logs visible too.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")

app = FastAPI(title="wabot")
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
    if mode != "subscribe" or token != settings.whatsapp_verify_token:
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
        raise HTTPException(status_code=401)

    payload = await request.json()
    for status in whatsapp.failed_statuses(payload):
        # E.g. 131047 when a free-form message is sent outside the 24 h window.
        log.warning("WhatsApp could not deliver %s: %s", status.get("id"), status.get("errors"))

    for msg in whatsapp.parse_messages(payload):
        trainee = trainees.find_by_phone(session, msg.phone)
        stored = Message(wamid=msg.wamid, direction="in", phone=msg.phone, type=msg.type)
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
            continue
        # Locally these run after the 200 is sent; in production they become Cloud Tasks.
        if trainee is None:
            background.add_task(reply_unregistered, msg)
        elif conversation.mentions_bot(msg.text):
            # Only messages addressed to the bot by name get an answer; the rest is just stored
            # and is part of the history the next time the bot is asked.
            background.add_task(reply, stored.id)

    return {"status": "ok"}


APOLOGY = "Przepraszam, mam chwilowy problem. Spróbuj proszę za kilka minut."
UNREGISTERED = "Ten numer nie jest zarejestrowany – skontaktuj się ze swoim trenerem."


def reply(message_id: int) -> None:
    """Answer a trainee's message that mentions the bot, and save what it reports."""
    settings = get_settings()
    now = utcnow()
    with SessionLocal() as session:
        message = session.get_one(Message, message_id)
        trainee = session.get_one(Trainee, message.trainee_id)
        history = conversation.recent_history(session, trainee.id, now)
    try:
        result = assistant.generate_reply(settings, trainee, history, now)
    except assistant.MissingApiKeyError as e:
        log.error("Can't reply to %s: %s", message.wamid, e)
        result = None
    except Exception:
        log.exception("LLM call for %s failed", message.wamid)
        result = None

    with SessionLocal() as session:
        if conversation.asked_again(session, message):
            log.info(
                "Not answering %s: the reply to the trainee's next message covers it", message.wamid
            )
            return
        text = save_report(session, trainee, message, result, settings.daily_updates_only)
    try:
        conversation.send_and_store(settings, trainee, text)
    except httpx.HTTPStatusError as e:
        # Meta explains the problem in the response body.
        log.error("Sending reply to %s failed: %s", message.wamid, e.response.text)
    except Exception:
        log.exception("Sending reply to %s failed", message.wamid)


def save_report(
    session: Session,
    trainee: Trainee,
    message: Message,
    result: LlmOutput | None,
    daily_updates_only: bool = False,
) -> str:
    """Save what the LLM extracted. Returns the text to send: the LLM's reply, or the apology
    if the LLM call or the save failed (the reply would confirm something that isn't saved)."""
    if result is None:
        return APOLOGY
    try:
        entries.save(session, trainee, message, result, daily_updates_only, now=utcnow())
    except Exception:
        log.exception("Saving the report from %s failed", message.wamid)
        return APOLOGY
    for kind, data in (("weight", result.weight), ("training", result.training)):
        if data is not None:
            log.info("Saved %s from %s: %s", kind, message.wamid, data.model_dump_json())
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
