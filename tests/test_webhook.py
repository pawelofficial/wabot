import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from wabot import assistant, whatsapp
from wabot.db import SessionLocal
from wabot.llm_output import LlmOutput
from wabot.models import Message


def text_payload(wamid: str, text: str, phone: str = "48111222333") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": phone,
                                    "id": wamid,
                                    "timestamp": "1761739200",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


def post_signed(client, payload: dict, secret: str = "test-secret"):
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={signature}"},
    )


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, str]]:
    """Capture outgoing messages instead of calling Meta."""
    calls = []

    def fake_send_text(settings, to, text):
        calls.append((to, text))
        return f"wamid.out{len(calls)}"

    monkeypatch.setattr(whatsapp, "send_text", fake_send_text)
    return calls


@pytest.fixture
def llm(monkeypatch) -> list[list[Message]]:
    """Replace the LLM with a stub; records the history it was given."""
    histories = []

    def fake_generate_reply(settings, history, now, training_plan):
        histories.append(history)
        return LlmOutput(reply=f"Odpowiedź {len(histories)}", weight=None, training=None)

    monkeypatch.setattr(assistant, "generate_reply", fake_generate_reply)
    return histories


def stored_messages() -> list[Message]:
    with SessionLocal() as session:
        return list(session.scalars(select(Message).order_by(Message.id)))


def test_verify_webhook(client):
    params = {"hub.mode": "subscribe", "hub.verify_token": "test-verify", "hub.challenge": "42"}
    response = client.get("/webhook", params=params)
    assert response.status_code == 200
    assert response.text == "42"


def test_verify_webhook_wrong_token(client):
    params = {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"}
    assert client.get("/webhook", params=params).status_code == 403


def test_rejects_bad_signature(client, sent):
    response = post_signed(client, text_payload("wamid.1", "hej"), secret="wrong")
    assert response.status_code == 401
    assert stored_messages() == []
    assert sent == []


def test_replies_with_the_llm_and_stores_both_messages(client, sent, llm):
    response = post_signed(client, text_payload("wamid.1", "yanek 82,4 rano"))
    assert response.status_code == 200
    assert sent == [("48111222333", "Odpowiedź 1")]

    inbound, outbound = stored_messages()
    assert (inbound.direction, inbound.wamid, inbound.body) == ("in", "wamid.1", "yanek 82,4 rano")
    assert (outbound.direction, outbound.wamid) == ("out", "wamid.out1")
    assert inbound.created_at.tzinfo is not None


def test_duplicate_delivery_is_ignored(client, sent, llm):
    post_signed(client, text_payload("wamid.1", "hej yanek"))
    post_signed(client, text_payload("wamid.1", "hej yanek"))
    assert len(sent) == 1
    assert [m.direction for m in stored_messages()] == ["in", "out"]


def test_status_updates_are_ignored(client, sent):
    payload = {
        "entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.x", "status": "read"}]}}]}]
    }
    assert post_signed(client, payload).status_code == 200
    assert stored_messages() == []
    assert sent == []


def test_history_includes_previous_turns_of_the_same_trainee(client, sent, llm):
    post_signed(client, text_payload("wamid.1", "yanek 82"))
    post_signed(client, text_payload("wamid.other", "hej yanek", phone="48999888777"))
    post_signed(client, text_payload("wamid.2", "kg"))
    post_signed(client, text_payload("wamid.3", "yanku, zanotuj"))

    last = llm[-1]
    assert [(m.direction, m.body) for m in last] == [
        ("in", "yanek 82"),
        ("out", "Odpowiedź 1"),
        ("in", "kg"),
        ("in", "yanku, zanotuj"),
    ]


def test_messages_without_the_bot_name_are_stored_but_not_answered(client, sent, llm):
    post_signed(client, text_payload("wamid.1", "82,4 rano"))
    assert llm == []
    assert sent == []
    assert [m.body for m in stored_messages()] == ["82,4 rano"]


def test_llm_turns_start_with_the_trainee():
    history = [
        Message(direction="out", phone="1", type="template", body=None),
        Message(direction="in", phone="1", type="text", body="hej"),
        Message(direction="out", phone="1", type="text", body="Cześć!"),
        Message(direction="in", phone="1", type="image", body=None),
    ]
    assert assistant.to_llm_messages(history) == [
        {"role": "user", "content": "hej"},
        {"role": "assistant", "content": "Cześć!"},
        {"role": "user", "content": "[wiadomość typu image]"},
    ]
