"""Telegram Bot API: webhook parsing, the webhook's secret and sending (the counterpart of
whatsapp.py; the bot uses one or the other by CHANNEL).

The bot token is part of every API URL (api.telegram.org/bot<token>/<method>), so nothing here
lets a URL reach a log: errors are raised as TelegramError with Telegram's own description, and
httpx's request log lines (which print the URL) are turned off below.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass

import httpx

from wabot.config import Settings

# httpx logs every request's URL at INFO; for Telegram that URL has the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://api.telegram.org/bot{token}/{method}"
FILE_URL = "https://api.telegram.org/file/bot{token}/{path}"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

# A keyboard with one button that sends the trainee's own number (the only way a bot can learn
# it), and the reply that takes the keyboard away again.
ASK_FOR_PHONE = {
    "keyboard": [[{"text": "Udostępnij numer telefonu", "request_contact": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
REMOVE_KEYBOARD = {"remove_keyboard": True}

# What a message is, by the field Telegram fills in: the first one present.
TYPES = (
    "text",
    "contact",
    "photo",
    "voice",
    "video",
    "video_note",
    "document",
    "sticker",
    "animation",
    "audio",
    "location",
)


class TelegramError(Exception):
    """A Bot API call failed. The text is Telegram's description, never the URL (it has the
    bot's token)."""


@dataclass(frozen=True)
class InboundMessage:
    external_id: str  # "tg:<chat id>:<message id>", unique like WhatsApp's wamid
    chat_id: int
    user_id: int | None  # the sender; in a private chat the same as chat_id
    private: bool  # a one-to-one chat with the bot, not a group
    type: str  # text, contact, photo, ... ("other" for anything else)
    text: str | None  # a text message's text, or a photo's (video's, ...) caption
    # A shared contact: its number, and whose Telegram account it is (the sender's own when
    # they pressed the "Udostępnij numer telefonu" button).
    contact_phone: str | None = None
    contact_user_id: int | None = None
    # A voice message: its file (download_file()) and length in seconds.
    voice_file_id: str | None = None
    voice_duration: int | None = None


def message_id(chat_id: int, telegram_message_id: int) -> str:
    """Messages.external_id of a Telegram message. Message IDs are counted per chat, and both
    directions share the count in a private chat."""
    return f"tg:{chat_id}:{telegram_message_id}"


def webhook_secret(settings: Settings) -> str:
    """The secret Telegram sends with every update (SECRET_HEADER), derived from the bot token:
    setWebhook (set_webhook()) gives it to Telegram, and nobody without the token knows it.
    Empty without a token, which rejects every update."""
    token = settings.telegram_bot_token
    if not token:
        return ""
    return hmac.new(token.encode(), b"wabot-telegram-webhook", hashlib.sha256).hexdigest()


def verify_secret(settings: Settings, header: str | None) -> bool:
    """Whether an update comes from Telegram: it carries the webhook's secret."""
    expected = webhook_secret(settings)
    return bool(expected) and header is not None and hmac.compare_digest(expected, header)


def parse_update(update: dict) -> InboundMessage | None:
    """The message in a webhook update, or None for anything else (an edited message, ...;
    set_webhook() asks only for messages)."""
    msg = update.get("message")
    if not isinstance(msg, dict) or "chat" not in msg:
        return None
    chat = msg["chat"]
    kind = next((t for t in TYPES if t in msg), "other")
    contact = msg.get("contact") or {}
    voice = msg.get("voice") or {}
    return InboundMessage(
        external_id=message_id(chat["id"], msg["message_id"]),
        chat_id=chat["id"],
        user_id=(msg.get("from") or {}).get("id"),
        private=chat.get("type") == "private",
        type=kind,
        # A caption counts as the message's text: "yanek 80,4" under a photo of the scale.
        text=msg.get("text") if kind == "text" else msg.get("caption"),
        contact_phone=contact.get("phone_number"),
        contact_user_id=contact.get("user_id"),
        voice_file_id=voice.get("file_id"),
        voice_duration=voice.get("duration"),
    )


def _call(settings: Settings, method: str, payload: dict | None = None) -> dict:
    """One Bot API call; Telegram's "result". Raises TelegramError."""
    if not settings.telegram_bot_token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set in .env")
    url = API.format(token=settings.telegram_bot_token, method=method)
    try:
        response = httpx.post(url, json=payload or {}, timeout=10)
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        # Not `from e`: httpx's exceptions carry the URL.
        raise TelegramError(f"{method}: {type(e).__name__}") from None
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('error_code')} {data.get('description')}")
    return data["result"]


def send_text(
    settings: Settings,
    chat_id: int,
    text: str,
    keyboard: dict | None = None,
    markdown: bool = False,
) -> str:
    """Send a text message. Returns its external ID. `keyboard`: ASK_FOR_PHONE or
    REMOVE_KEYBOARD. `markdown`: *bold* and _italic_ as formatting (the model writes WhatsApp's
    formatting, which is Telegram's legacy Markdown too); if Telegram can't parse it (a stray
    * or _), the text goes out as it is."""
    payload = {"chat_id": chat_id, "text": text}
    if keyboard is not None:
        payload["reply_markup"] = keyboard
    if markdown:
        try:
            sent = _call(settings, "sendMessage", {**payload, "parse_mode": "Markdown"})
            return message_id(chat_id, sent["message_id"])
        except TelegramError as e:
            if "can't parse entities" not in str(e):
                raise
    sent = _call(settings, "sendMessage", payload)
    return message_id(chat_id, sent["message_id"])


def download_file(settings: Settings, file_id: str) -> bytes:
    """A file someone sent the bot (a voice message, ...), by its file_id. Bots can download
    files up to 20 MB. Raises TelegramError (never with the URL: it has the token)."""
    path = _call(settings, "getFile", {"file_id": file_id})["file_path"]
    url = FILE_URL.format(token=settings.telegram_bot_token, path=path)
    try:
        response = httpx.get(url, timeout=30)
    except httpx.HTTPError as e:
        raise TelegramError(f"download: {type(e).__name__}") from None
    if response.status_code != 200:
        raise TelegramError(f"download: HTTP {response.status_code}")
    return response.content


def set_webhook(settings: Settings, url: str) -> None:
    """Have Telegram send the bot's messages to `url`, with the webhook's secret."""
    _call(
        settings,
        "setWebhook",
        {"url": url, "secret_token": webhook_secret(settings), "allowed_updates": ["message"]},
    )


def bot_username(settings: Settings) -> str:
    """The bot's @username (without the @); the link trainees open is t.me/<username>."""
    return _call(settings, "getMe")["username"]
