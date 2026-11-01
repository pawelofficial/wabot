import logging
from datetime import datetime, timedelta
from typing import Annotated

import anthropic
import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wabot import assistant, whatsapp
from wabot.config import Settings, get_settings
from wabot.db import SessionLocal, get_session
from wabot.models import Message, utcnow

log = logging.getLogger(__name__)

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

    for msg in whatsapp.parse_messages(await request.json()):
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


HISTORY_WINDOW = timedelta(hours=24)
HISTORY_LIMIT = 20


def recent_history(session: Session, phone: str, now: datetime) -> list[Message]:
    """The trainee's last messages within the history window, oldest first."""
    newest_first = session.scalars(
        select(Message)
        .where(Message.phone == phone, Message.created_at >= now - HISTORY_WINDOW)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(HISTORY_LIMIT)
    )
    return list(reversed(newest_first.all()))


def reply(msg: whatsapp.InboundMessage) -> None:
    settings = get_settings()
    if msg.text is None:
        text = "Na razie obsługuję tylko wiadomości tekstowe."
    else:
        now = utcnow()
        with SessionLocal() as session:
            history = recent_history(session, msg.phone, now)
        try:
            text = assistant.generate_reply(settings, history, now)
        except anthropic.APIError:
            log.exception("Claude call for %s failed", msg.wamid)
            text = "Przepraszam, mam chwilowy problem. Spróbuj proszę za kilka minut."
    try:
        wamid = whatsapp.send_text(settings, msg.phone, text)
    except httpx.HTTPStatusError as e:
        # Meta explains the problem in the response body.
        log.error("Sending reply to %s failed: %s", msg.wamid, e.response.text)
        return
    except Exception:
        log.exception("Sending reply to %s failed", msg.wamid)
        return
    with SessionLocal() as session:
        session.add(Message(wamid=wamid, direction="out", phone=msg.phone, type="text", body=text))
        session.commit()
