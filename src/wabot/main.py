import logging
from typing import Annotated

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wabot import assistant, conversation, whatsapp
from wabot.config import Settings, get_settings
from wabot.db import SessionLocal, get_session
from wabot.models import Message, utcnow

log = logging.getLogger(__name__)
# uvicorn configures only its own loggers; this makes the app's INFO logs visible too.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")

app = FastAPI(title="wabot")

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
        session.add(
            Message(wamid=msg.wamid, direction="in", phone=msg.phone, type=msg.type, body=msg.text)
        )
        try:
            session.commit()
        except IntegrityError:
            # Meta redelivered a message we already stored.
            session.rollback()
            continue
        # Locally this runs after the 200 is sent; in production it becomes a Cloud Task.
        background.add_task(reply, msg)

    return {"status": "ok"}


APOLOGY = "Przepraszam, mam chwilowy problem. Spróbuj proszę za kilka minut."


def reply(msg: whatsapp.InboundMessage) -> None:
    # Only messages addressed to the bot by name get an answer; the rest is just stored, and
    # is part of the history the next time the bot is asked.
    if not conversation.mentions_bot(msg.text):
        return
    settings = get_settings()
    now = utcnow()
    with SessionLocal() as session:
        history = conversation.recent_history(session, msg.phone, now)
    plan = conversation.training_plan_for(settings, msg.phone)
    try:
        result = assistant.generate_reply(settings, history, now, plan)
        text = result.reply
    except assistant.MissingApiKeyError as e:
        log.error("Can't reply to %s: %s", msg.wamid, e)
        text = APOLOGY
    except Exception:
        log.exception("LLM call for %s failed", msg.wamid)
        text = APOLOGY
    else:
        # Not saved anywhere yet (milestone 2); logged so it can be checked.
        for kind, data in (("weight", result.weight), ("training", result.training)):
            if data is not None:
                log.info("Extracted %s from %s: %s", kind, msg.wamid, data.model_dump_json())
    try:
        conversation.send_and_store(settings, msg.phone, text)
    except httpx.HTTPStatusError as e:
        # Meta explains the problem in the response body.
        log.error("Sending reply to %s failed: %s", msg.wamid, e.response.text)
    except Exception:
        log.exception("Sending reply to %s failed", msg.wamid)
