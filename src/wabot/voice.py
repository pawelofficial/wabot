"""Voice messages: the trainee's recording turned into text (OpenAI's transcription), which is
then stored as the message's text and read like a typed message (docs/CONVERSATION.md, case 11).
Telegram only; Telegram's own transcription is for Premium users in the app, bots don't get it.
"""

import logging

import openai

from wabot import telegram
from wabot.config import Settings

log = logging.getLogger(__name__)

# Tells the model the language and the words it's likely to hear, so "yanek" and training
# vocabulary come out right.
PROMPT = "Yanek, raport treningowy: waga 80,5 kg, przysiady, wyciskanie, martwy ciąg, serie."


def transcribe(settings: Settings, audio: bytes) -> str:
    """The text of a recording (Telegram's voice messages are OGG/Opus). Raises on failure."""
    client = openai.OpenAI(api_key=settings.openai_api_key, timeout=60, max_retries=1)
    result = client.audio.transcriptions.create(
        model=settings.openai_transcribe_model,
        file=("voice.ogg", audio, "audio/ogg"),
        language="pl",
        prompt=PROMPT,
    )
    return result.text.strip()


def text_of(settings: Settings, external_id: str, file_id: str, duration: int | None) -> str | None:
    """The text of a Telegram voice message, or None if it's too long, empty or something failed
    (logged; the trainee gets a fixed reply)."""
    if duration is not None and duration > settings.voice_max_seconds:
        log.info(
            "Voice message %s is %d s, longer than VOICE_MAX_SECONDS=%d: not transcribed",
            external_id,
            duration,
            settings.voice_max_seconds,
        )
        return None
    if not settings.openai_api_key:
        log.error("Can't transcribe %s: OPENAI_API_KEY is not set in .env", external_id)
        return None
    try:
        audio = telegram.download_file(settings, file_id)
    except telegram.TelegramError as e:
        log.error("Downloading voice message %s failed: %s", external_id, e)
        return None
    try:
        text = transcribe(settings, audio)
    except Exception:
        log.exception("Transcribing voice message %s failed", external_id)
        return None
    if not text:
        log.info("Voice message %s: nothing recognised", external_id)
        return None
    log.info("Transcribed voice message %s (%s s, %d chars)", external_id, duration, len(text))
    log.debug("Transcript of %s: %r", external_id, text)
    return text
