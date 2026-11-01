"""Claude: turns a trainee's chat history into the bot's next reply."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from wabot.config import Settings
from wabot.models import Message

log = logging.getLogger(__name__)

# Kept free of dates and other per-request values so it stays cacheable.
SYSTEM_PROMPT = """\
You are a WhatsApp assistant working on behalf of a personal trainer. You talk \
with the trainer's clients (trainees). Trainees send you short reports about \
their body weight, their training and how they are sticking to their diet.

Always reply in Polish, in a friendly, brief, natural tone – this is WhatsApp, \
so one to three short sentences, no headings or lists. WhatsApp formatting only \
(*bold*, _italics_), no Markdown.

What you do:
- Acknowledge reports and repeat back what you understood (e.g. weight with \
units, which training was done, how the diet went), so the trainee can correct \
you.
- If a report is ambiguous (missing unit, unclear day, unclear what "zrobione" \
refers to), ask one short clarifying question.
- Interpret relative dates ("wczoraj", "w poniedziałek") using today's date \
given in the conversation.

What you don't do:
- No training, diet, supplement or medical advice of your own – say that's a \
question for their trainer.
- You don't yet have access to the trainee's training or diet plan; if asked \
about it, say so and refer them to the trainer.
- Stay on topic: training, weight, diet and the arrangement with the trainer.

This is an early prototype: reports are not saved anywhere yet. Don't claim \
you've saved anything; say you "noted" it ("zanotowałem") at most.
"""

WEEKDAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def to_claude_messages(history: list[Message]) -> list[dict]:
    """Map stored messages (oldest first) to Claude's user/assistant turns."""
    turns = []
    for msg in history:
        role = "user" if msg.direction == "in" else "assistant"
        text = msg.body if msg.body else f"[wiadomość typu {msg.type}]"
        turns.append({"role": role, "content": text})
    # The conversation must start with the trainee.
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def generate_reply(settings: Settings, history: list[Message], now: datetime) -> str:
    local = now.astimezone(ZoneInfo(settings.default_timezone))
    today = f"Dzisiaj jest {WEEKDAYS_PL[local.weekday()]}, {local:%Y-%m-%d}, godz. {local:%H:%M}."

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.beta.messages.create(
        model=settings.claude_model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        cache_control={"type": "ephemeral"},
        output_config={"effort": "low"},
        # If a safety classifier declines, the API retries on a fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[
            *to_claude_messages(history),
            # Per-request context goes after the history so the prefix stays cacheable.
            {"role": "system", "content": today},
        ],
    )
    if response.stop_reason == "refusal":
        log.warning("Claude refused: %s", response.stop_details)
        return "Przepraszam, nie mogę w tym pomóc. Zapytaj proszę swojego trenera."

    return "".join(block.text for block in response.content if block.type == "text").strip()
