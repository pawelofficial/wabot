"""Send a message the bot starts itself, composed by the app the same way it will be in production.

With HARDCODED_FIRST_MESSAGE=True in .env the text comes from
wabot.utils.hardcoded_first_message(); otherwise the LLM (OpenAI) writes it
(needs OPENAI_API_KEY). The message is stored as chat history, so when you reply
the bot sees what it asked.

WhatsApp delivers free-form messages only within 24 h of your last message to
the bot. Outside that window Meta accepts the request but drops the message
(the running bot logs error 131047), so message the bot first.

Run from the project root:
    uv run python scripts/send_test_custom_first_message.py [--to 48123456789] [--dry-run]
"""

import argparse
import sys

import httpx

from wabot import assistant, conversation, trainees
from wabot.config import get_settings
from wabot.db import SessionLocal
from wabot.models import utcnow


def main() -> None:
    # Windows consoles default to cp1252, which can't print Polish letters or emoji.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", help="recipient, digits only (default: WHATSAPP_TEST_RECIPIENT)")
    parser.add_argument("--dry-run", action="store_true", help="print the message, don't send")
    args = parser.parse_args()

    settings = get_settings()
    to = args.to or settings.whatsapp_test_recipient
    if not to:
        sys.exit("No recipient: pass --to or set WHATSAPP_TEST_RECIPIENT in .env")

    now = utcnow()
    with SessionLocal() as session:
        trainee = trainees.find_by_phone(session, to)
        if trainee is None:
            sys.exit(f"{to} isn't a registered trainee – run scripts/add_trainee.py first")
        history = conversation.recent_history(session, trainee.id, now)
        in_window = conversation.in_service_window(session, to, now)

    if settings.hardcoded_first_message:
        source = "utils.hardcoded_first_message()"
    else:
        source = f"OpenAI ({settings.openai_model})"
    try:
        text = conversation.compose_first_message(settings, trainee, history, now)
    except assistant.MissingApiKeyError:
        sys.exit("No OPENAI_API_KEY in .env – set it, or set HARDCODED_FIRST_MESSAGE=True")
    print(f"Message from {source}:\n\n{text}\n")

    if args.dry_run:
        return
    if not in_window:
        print(
            f"WARNING: the bot has no message from {to} in the last 24 h. WhatsApp will "
            "probably not deliver this – send the bot any message first, then rerun.",
            file=sys.stderr,
        )

    try:
        wamid = conversation.send_and_store(settings, trainee, text)
    except httpx.HTTPStatusError as e:
        sys.exit(f"Sending failed: HTTP {e.response.status_code}: {e.response.text}")
    print(f"Sent to {to}: {wamid}")


if __name__ == "__main__":
    main()
