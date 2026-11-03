"""The LLM (OpenAI): turns a trainee's chat history into the bot's next reply, plus the
weight and training data it reports."""

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openai
import yaml

from wabot import exercises, llm_output
from wabot.config import Settings
from wabot.llm_output import LlmOutput
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


REFUSAL = "Przepraszam, nie mogę w tym pomóc. Zapytaj proszę swojego trenera."


def build_instructions(training_plan: str | None, task: str | None = None) -> str:
    """System prompt with the trainee's plan injected. The static part comes first so the
    shared prefix stays cacheable across trainees."""
    if training_plan:
        plan = PROMPTS["training_plan"].replace("{training_plan}", training_plan.strip())
    else:
        plan = PROMPTS["no_training_plan"]
    catalog = PROMPTS["exercise_catalog"].replace("{exercise_catalog}", exercises.catalog_text())
    parts = [PROMPTS["system"], catalog, plan]
    if task:
        parts.append(task)
    return "\n".join(parts)


# How many times the model may try to return output that passes validate_output().
ATTEMPTS = 2


def _complete(
    settings: Settings,
    instructions: str,
    messages: list[dict],
    now: datetime,
    training_plan: str | None,
) -> LlmOutput:
    if not settings.openai_api_key:
        raise MissingApiKeyError("OPENAI_API_KEY is not set in .env")

    client = openai.OpenAI(api_key=settings.openai_api_key)
    today = now.astimezone(ZoneInfo(settings.default_timezone)).date()
    messages = list(messages)
    for attempt in range(1, ATTEMPTS + 1):
        response = client.responses.create(
            model=settings.openai_model,
            instructions=instructions,
            input=messages,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "yanek_output",
                    "schema": llm_output.json_schema(),
                    "strict": True,
                }
            },
            reasoning={"effort": "low"},
            max_output_tokens=4000,
            # Trainees' messages: don't keep the response stored on OpenAI's side.
            store=False,
        )
        for item in response.output:
            for part in getattr(item, "content", None) or []:
                if part.type == "refusal":
                    log.warning("Model refused: %s", part.refusal)
                    return LlmOutput(reply=REFUSAL, weight=None, training=None)

        raw = response.output_text
        if not raw:
            raise RuntimeError(f"Empty response ({response.status}: {response.incomplete_details})")
        try:
            return llm_output.validate_output(raw, today, training_plan)
        except llm_output.InvalidOutputError as e:
            log.warning("Invalid LLM output (attempt %d/%d): %s", attempt, ATTEMPTS, e)
            if attempt == ATTEMPTS:
                raise
            # Show the model its answer and what's wrong with it, and let it try again.
            messages += [
                {"role": "assistant", "content": raw},
                {
                    "role": "developer",
                    "content": "Twoja odpowiedź nie przeszła walidacji:\n- "
                    + "\n- ".join(e.errors)
                    + "\nPopraw ją i zwróć cały obiekt JSON jeszcze raz.",
                },
            ]
    raise AssertionError("unreachable")


def generate_reply(
    settings: Settings, history: list[Message], now: datetime, training_plan: str | None
) -> LlmOutput:
    """Reply to the trainee's latest message (the last entry of history) and extract the
    weight and training it reports."""
    return _complete(
        settings,
        build_instructions(training_plan),
        [
            *to_llm_messages(history),
            # Per-request context goes after the history so the prefix stays cacheable.
            {"role": "developer", "content": _today(settings, now)},
        ],
        now,
        training_plan,
    )


def generate_first_message(
    settings: Settings, history: list[Message], now: datetime, training_plan: str | None
) -> str:
    """A message the bot sends on its own, e.g. to ask for a report."""
    output = _complete(
        settings,
        build_instructions(training_plan, PROMPTS["first_message_task"]),
        [
            *to_llm_messages(history),
            # The API needs the conversation to end with a user turn.
            {"role": "user", "content": f"[automatyczny wyzwalacz] {_today(settings, now)}"},
        ],
        now,
        training_plan,
    )
    return output.reply
