"""The LLM (OpenAI): turns a trainee's chat history into the bot's next reply, plus the
weight and training data it reports."""

import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openai
import yaml

from wabot import llm_output, plans
from wabot.config import Settings
from wabot.llm_output import LlmOutput
from wabot.models import Message, Trainee
from wabot.plans import TrainingPlan

log = logging.getLogger(__name__)

# LLM prompts live in prompts.yaml; read once at import.
PROMPTS = yaml.safe_load(Path(__file__).with_name("prompts.yaml").read_text(encoding="utf-8"))

WEEKDAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def to_llm_messages(history: list[Message]) -> list[dict]:
    """Map stored messages (oldest first) to the model's user/assistant turns.

    The turns start with the trainee. Bot messages before the trainee's first one (a reminder,
    a message the bot started) are kept as a developer note, so the model knows what the
    trainee is answering."""
    turns = []
    earlier_bot = []
    for msg in history:
        text = msg.body if msg.body else f"[wiadomość typu {msg.type}]"
        if msg.direction == "in":
            turns.append({"role": "user", "content": text})
        elif turns:
            turns.append({"role": "assistant", "content": text})
        else:
            earlier_bot.append(text)
    if earlier_bot:
        note = PROMPTS["earlier_bot_messages"].strip() + "\n" + "\n".join(earlier_bot)
        turns.insert(0, {"role": "developer", "content": note})
    return turns


def split_at_last_bot_message(history: list[Message]) -> tuple[list[Message], list[Message]]:
    """(context, new): new is everything after the bot's last message, i.e. the report the
    trainee is sending now; context is the rest. Without a bot message, everything is new."""
    for i in range(len(history) - 1, -1, -1):
        if history[i].direction == "out":
            return history[: i + 1], history[i + 1 :]
    return [], history


def reply_input(settings: Settings, history: list[Message]) -> list[dict]:
    """The chat turns for a reply call, shaped by HISTORY_MODE (see config.py)."""
    context, new = split_at_last_bot_message(history)
    context_turns = to_llm_messages(context)
    if settings.history_mode == "new_only" or not context_turns:
        return to_llm_messages(new)
    marker = {"role": "developer", "content": PROMPTS["new_messages_marker"].strip()}
    return [*context_turns, marker, *to_llm_messages(new)]


class MissingApiKeyError(Exception):
    pass


def _local(trainee: Trainee, now: datetime) -> datetime:
    return now.astimezone(ZoneInfo(trainee.timezone))


def _today(trainee: Trainee, now: datetime) -> str:
    local = _local(trainee, now)
    return f"Dzisiaj jest {WEEKDAYS_PL[local.weekday()]}, {local:%Y-%m-%d}, godz. {local:%H:%M}."


REFUSAL = "Przepraszam, nie mogę w tym pomóc. Zapytaj proszę swojego trenera."


def build_instructions(
    plan: TrainingPlan | None, trainee_name: str | None = None, task: str | None = None
) -> str:
    """System prompt with the trainee's name and plan injected (the plan's exercises with
    their catalog IDs, names, aliases and replacements; not the whole catalog). The static
    part comes first so the shared prefix stays cacheable across trainees."""
    parts = [PROMPTS["system"]]
    if trainee_name:
        parts.append(PROMPTS["trainee_name"].replace("{trainee_name}", trainee_name.strip()))
    if plan:
        plan_part = PROMPTS["training_plan"].replace("{training_plan}", plan.prompt_text())
    else:
        plan_part = PROMPTS["no_training_plan"]
    parts.append(plan_part)
    if task:
        parts.append(task)
    return "\n".join(parts)


# How many times the model may try to return output that passes validate_output().
ATTEMPTS = 2


def _complete(
    settings: Settings,
    instructions: str,
    messages: list[dict],
    today: date,
    plan: TrainingPlan | None,
) -> LlmOutput:
    """Call the model; today (the trainee's local date) and the plan are for validation."""
    if not settings.openai_api_key:
        raise MissingApiKeyError("OPENAI_API_KEY is not set in .env")

    # The SDK default is 10 minutes and 2 retries; the trainee is waiting for the reply.
    client = openai.OpenAI(api_key=settings.openai_api_key, timeout=60, max_retries=1)
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
            # Letters from the instructions (the trainee's name) and the chat are allowed in
            # the reply even when they aren't Latin, e.g. a Ukrainian name.
            seen = instructions + "".join(str(m["content"]) for m in messages)
            return llm_output.validate_output(raw, today, plan, allowed_letters=seen)
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
    settings: Settings, trainee: Trainee, history: list[Message], now: datetime
) -> LlmOutput:
    """Reply to the trainee's latest message (the last entry of history) and extract the
    weight and training it reports. The trainee's name and plan go into the prompt."""
    plan = plans.of(trainee)
    return _complete(
        settings,
        build_instructions(plan, trainee.name),
        [
            *reply_input(settings, history),
            # Per-request context goes after the history so the prefix stays cacheable.
            {"role": "developer", "content": _today(trainee, now)},
        ],
        _local(trainee, now).date(),
        plan,
    )


def generate_first_message(
    settings: Settings, trainee: Trainee, history: list[Message], now: datetime
) -> str:
    """A message the bot sends on its own, e.g. to ask for a report."""
    plan = plans.of(trainee)
    output = _complete(
        settings,
        build_instructions(plan, trainee.name, PROMPTS["first_message_task"]),
        [
            *to_llm_messages(history),
            # The API needs the conversation to end with a user turn.
            {"role": "user", "content": f"[automatyczny wyzwalacz] {_today(trainee, now)}"},
        ],
        _local(trainee, now).date(),
        plan,
    )
    return output.reply
