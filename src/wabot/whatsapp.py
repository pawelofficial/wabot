"""Meta WhatsApp Cloud API: webhook parsing, signature checks and sending."""

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
    text: str | None


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check the X-Hub-Signature-256 header Meta sends with every webhook."""
    if not header or not header.startswith("sha256="):
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
                text = msg.get("text", {}).get("body") if msg["type"] == "text" else None
                messages.append(
                    InboundMessage(wamid=msg["id"], phone=msg["from"], type=msg["type"], text=text)
                )
    return messages


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
