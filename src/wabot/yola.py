"""Yola: plain GPT in Yanek's Telegram chat (an experiment, outside docs/PLAN.md; 2026-11-25).

A message that mentions "yola", from a trainee in YOLA_TRAINEE_IDS, goes straight to the model
with Yola's one-line prompt instead of Yanek's: no plan, no extraction. main.py catches it before
anything is stored, so the database never sees it and Yanek's history stays as it was.

The most raw version on purpose: Yola's memory is the last MEMORY messages per chat, in this
process only (gone after a restart; Cloud Run restarts after ~15 idle minutes and on every
deploy); no dedup (a Telegram redelivery is answered again). The answer goes out YOLA_DELAY
seconds after the message (the model's time included), still inside the request on Cloud Run.
"""

import logging
import re
import threading
import time
from collections import deque

import openai

from wabot import telegram
from wabot.config import Settings

log = logging.getLogger(__name__)

PROMPT = (
    "odpowiadaj po polsku, masz na imie yola i jestes moja znajoma. "
    "udawaj czlowieka i badz bardzo wygadana, mozesz zmyslac co robisz, czym sie interesujesz "
    "itp itd"
)
# "yola", and its Polish forms ("yoli", "yolę", "yolą", "yolu"); not "yolo".
NAME = re.compile(r"\byol(?:a|i|ę|ą|u)\b", re.IGNORECASE)
RESET = re.compile(r"\W*yola\W+reset\W*", re.IGNORECASE)
MEMORY = 20  # messages (yours and Yola's) kept per chat
TELEGRAM_MAX = 4096  # characters in one Telegram message
FAILED = "Sorki, coś mi nie wyszło. Napisz jeszcze raz."
FORGOT = "Okej, zaczynamy od nowa."

_memory: dict[int, deque] = {}
_lock = threading.Lock()


def calls_yola(text: str | None) -> bool:
    return text is not None and NAME.search(text) is not None


def _ask(settings: Settings, history: list[dict], text: str) -> str:
    client = openai.OpenAI(api_key=settings.openai_api_key, timeout=60, max_retries=1)
    response = client.responses.create(
        model=settings.openai_model,
        instructions=PROMPT,
        input=[*history, {"role": "user", "content": text}],
        reasoning={"effort": "low"},
        max_output_tokens=4000,
        store=False,
    )
    return response.output_text.strip()


def _chunks(text: str) -> list[str]:
    """The answer in pieces Telegram takes, cut at a line break where possible."""
    pieces = []
    text = text.strip()
    while len(text) > TELEGRAM_MAX:
        cut = text.rfind("\n", 0, TELEGRAM_MAX)
        cut = cut if cut > 0 else TELEGRAM_MAX
        pieces.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    # Telegram refuses an empty message.
    return [p for p in [*pieces, text] if p]


def answer(settings: Settings, chat_id: int, text: str) -> None:
    """Ask the model with this chat's memory and send the answer YOLA_DELAY seconds after the
    message came in. "yola reset" forgets the chat."""
    started = time.monotonic()
    if RESET.fullmatch(text):
        with _lock:
            _memory.pop(chat_id, None)
        reply = FORGOT
    else:
        with _lock:
            history = list(_memory.get(chat_id, ()))
        try:
            reply = _ask(settings, history, text) or FAILED
        except Exception:
            log.exception("Yola: the model call failed")
            reply = FAILED
        else:
            with _lock:
                memory = _memory.setdefault(chat_id, deque(maxlen=MEMORY))
                memory.append({"role": "user", "content": text})
                memory.append({"role": "assistant", "content": reply})
    wait = settings.yola_delay - (time.monotonic() - started)
    if wait > 0:
        time.sleep(wait)
    try:
        for piece in _chunks(reply):
            telegram.send_text(settings, chat_id, piece)
    except telegram.TelegramError as e:
        log.error("Yola: sending to Telegram chat %s failed: %s", chat_id, e)
        return
    log.info("Yola answered chat %s", chat_id)
