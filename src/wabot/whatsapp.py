"""Meta WhatsApp Cloud API: webhook parsing, signature checks and sending (the counterpart of
telegram.py; the bot uses one or the other by CHANNEL)."""

import hashlib
import hmac
from dataclasses import dataclass

import httpx

from wabot.config import Settings


@dataclass(frozen=True)
class InboundMessage:
    wamid: str
    phone: str
    type: str
    text: str | None  # a text message's text, or an image's (video's, document's) caption


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check the X-Hub-Signature-256 header Meta sends with every webhook. Without an app
    secret nothing passes (anyone could sign with an empty key)."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_messages(payload: dict) -> list[InboundMessage]:
    """Extract inbound messages from a webhook payload.

    Delivery status updates (sent, delivered, read) arrive on the same webhook
    without a "messages" key and are skipped.
    """
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                kind = msg["type"]
                if kind == "text":
                    text = msg.get("text", {}).get("body")
                else:
                    # A caption counts as the message's text, like on Telegram.
                    text = (msg.get(kind) or {}).get("caption")
                messages.append(
                    InboundMessage(wamid=msg["id"], phone=msg["from"], type=msg["type"], text=text)
                )
    return messages


def failed_statuses(payload: dict) -> list[dict]:
    """Delivery status updates that report a failed outbound message."""
    return [
        status
        for entry in payload.get("entry", [])
        for change in entry.get("changes", [])
        for status in change.get("value", {}).get("statuses", [])
        if status.get("status") == "failed"
    ]


def send_text(settings: Settings, to: str, text: str) -> str:
    """Send a free-form text message; only allowed within 24 h of the trainee's last message.

    Returns the wamid of the sent message.
    """
    url = (
        f"https://graph.facebook.com/{settings.graph_api_version}"
        f"/{settings.whatsapp_phone_number_id}/messages"
    )
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["messages"][0]["id"]
