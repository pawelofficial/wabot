"""The LLM (OpenAI): turns a trainee's chat history into the bot's next reply."""

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openai
import yaml

from wabot.config import Settings
from wabot.models import Message

log = logging.getLogger(__name__)

# LLM prompts live in prompts.yaml; read once at import.
PROMPTS = yaml.safe_load(Path(__file__).with_name("prompts.yaml").read_text(encoding="utf-8"))

WEEKDAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def to_llm_messages(history: list[Message]) -> list[dict]:
    """Map stored messages (oldest first) to the model's user/assistant turns."""
    turns = []
    for msg in history:
        role = "user" if msg.direction == "in" else "assistant"
        text = msg.body if msg.body else f"[wiadomość typu {msg.type}]"
        turns.append({"role": role, "content": text})
    # The conversation must start with the trainee.
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


class MissingApiKeyError(Exception):
    pass


def _today(settings: Settings, now: datetime) -> str:
    local = now.astimezone(ZoneInfo(settings.default_timezone))
    return f"Dzisiaj jest {WEEKDAYS_PL[local.weekday()]}, {local:%Y-%m-%d}, godz. {local:%H:%M}."


def _complete(settings: Settings, instructions: str, messages: list[dict]) -> str:
    if not settings.openai_api_key:
        raise MissingApiKeyError("OPENAI_API_KEY is not set in .env")

    client = openai.OpenAI(api_key=settings.openai_api_key)
    response = client.responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=messages,
        reasoning={"effort": "low"},
        max_output_tokens=2000,
        # Trainees' messages: don't keep the response stored on OpenAI's side.
        store=False,
    )
    for item in response.output:
        for part in getattr(item, "content", None) or []:
            if part.type == "refusal":
                log.warning("Model refused: %s", part.refusal)
                return "Przepraszam, nie mogę w tym pomóc. Zapytaj proszę swojego trenera."

    text = response.output_text.strip()
    if not text:
        raise RuntimeError(f"Empty response ({response.status}: {response.incomplete_details})")
    return text


def generate_reply(settings: Settings, history: list[Message], now: datetime) -> str:
    """Reply to the trainee's latest message (the last entry of history)."""
    return _complete(
        settings,
        PROMPTS["system"],
        [
            *to_llm_messages(history),
            # Per-request context goes after the history so the prefix stays cacheable.
            {"role": "developer", "content": _today(settings, now)},
        ],
    )


def generate_first_message(settings: Settings, history: list[Message], now: datetime) -> str:
    """A message the bot sends on its own, e.g. to ask for a report."""
    return _complete(
        settings,
        PROMPTS["system"] + "\n" + PROMPTS["first_message_task"],
        [
            *to_llm_messages(history),
            # The API needs the conversation to end with a user turn.
            {"role": "user", "content": f"[automatyczny wyzwalacz] {_today(settings, now)}"},
        ],
    )
