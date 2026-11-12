"""Check that the active messenger (CHANNEL in .env) works: run.sh runs this on every start.

Telegram (CHANNEL=telegram): asks Telegram for the bot's name with TELEGRAM_BOT_TOKEN and prints
the link trainees open (t.me/<bot>). If WHATSAPP_TEST_RECIPIENT (your own number) is a trainee
who has shared their number with the bot, it also sends you a test message.

WhatsApp (CHANNEL=whatsapp): sends a template message to WHATSAPP_TEST_RECIPIENT through the
Meta Cloud API (WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID).

Usage:
    uv run python scripts/send_test.py [template_name] [language_code]   (template: WhatsApp)
"""

import json
import sys

import httpx
from sqlalchemy.exc import OperationalError

from wabot import telegram, trainees
from wabot.config import Settings, get_settings
from wabot.db import SessionLocal

TEST_TEXT = "Test: bot działa."


def check_telegram(settings: Settings) -> None:
    try:
        username = telegram.bot_username(settings)
    except telegram.TelegramError as e:
        sys.exit(f"Telegram: {e} – check TELEGRAM_BOT_TOKEN in .env")
    print(f"Telegram bot @{username} works. Trainees open it at https://t.me/{username}")

    phone = settings.whatsapp_test_recipient
    if not phone:
        return
    try:
        with SessionLocal() as session:
            trainee = trainees.find_by_phone(session, trainees.normalize_phone(phone))
    except (ValueError, OperationalError):
        return  # not a number, or no database yet
    if trainee is None:
        print(f"{phone} isn't a trainee yet: uv run python scripts/add_trainee.py")
    elif trainee.telegram_chat_id is None:
        print(
            f"To chat with the bot as {trainee.name}: open https://t.me/{username} on the phone "
            f"with {phone} and press „Udostępnij numer telefonu”"
        )
    else:
        try:
            telegram.send_text(settings, trainee.telegram_chat_id, TEST_TEXT)
        except telegram.TelegramError as e:
            sys.exit(f"Sending a test message to {trainee.name} failed: {e}")
        print(f"Test message sent to {trainee.name} on Telegram")


def send_whatsapp_template(settings: Settings, template: str, language: str) -> None:
    response = httpx.post(
        f"https://graph.facebook.com/{settings.graph_api_version}"
        f"/{settings.whatsapp_phone_number_id}/messages",
        headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
        json={
            "messaging_product": "whatsapp",
            "to": settings.whatsapp_test_recipient,
            "type": "template",
            "template": {"name": template, "language": {"code": language}},
        },
        timeout=10,
    )
    if response.is_error:
        # Meta returns a JSON error body explaining what went wrong.
        sys.exit(f"HTTP {response.status_code}: {response.text}")
    print(json.dumps(response.json(), indent=2))


def main() -> None:
    # Windows consoles default to cp1252, which can't print Polish letters.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    settings = get_settings()
    if missing := settings.missing_for_channel():
        sys.exit(f"CHANNEL={settings.channel} needs {', '.join(missing)} in .env")
    if settings.channel == "telegram":
        check_telegram(settings)
    else:
        if not settings.whatsapp_test_recipient:
            sys.exit("Set WHATSAPP_TEST_RECIPIENT in .env (your number, digits only)")
        template = sys.argv[1] if len(sys.argv) > 1 else "jaspers_market_plain_text_v1"
        language = sys.argv[2] if len(sys.argv) > 2 else "en_US"
        send_whatsapp_template(settings, template, language)


if __name__ == "__main__":
    main()
