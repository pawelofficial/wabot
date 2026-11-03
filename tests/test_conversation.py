import re
from datetime import timedelta
from pathlib import Path

import pytest

from wabot import assistant, conversation, exercises, main, utils
from wabot.config import Settings
from wabot.db import SessionLocal
from wabot.models import Message, utcnow

from .test_webhook import post_signed, text_payload


def add_message(phone: str, direction: str, body: str, age: timedelta) -> None:
    with SessionLocal() as session:
        session.add(
            Message(
                direction=direction, phone=phone, type="text", body=body, created_at=utcnow() - age
            )
        )
        session.commit()


def test_hardcoded_first_message_skips_the_llm(monkeypatch):
    def fail(*args):
        raise AssertionError("The LLM must not be called")

    monkeypatch.setattr(assistant, "generate_first_message", fail)
    settings = Settings(hardcoded_first_message=True)
    text = conversation.compose_first_message(settings, "48111222333", [], utcnow())
    assert text == utils.hardcoded_first_message()


def test_first_message_comes_from_the_llm_by_default(monkeypatch):
    monkeypatch.setattr(assistant, "generate_first_message", lambda *args: "Cześć od modelu")
    text = conversation.compose_first_message(Settings(), "48111222333", [], utcnow())
    assert text == "Cześć od modelu"


def test_first_message_without_api_key_raises():
    with pytest.raises(assistant.MissingApiKeyError):
        conversation.compose_first_message(Settings(openai_api_key=""), "48111222333", [], utcnow())


def test_service_window_needs_a_recent_inbound_message():
    phone = "48111222333"
    add_message(phone, "in", "hej", age=timedelta(hours=30))
    add_message(phone, "out", "Cześć!", age=timedelta(hours=1))
    assert not conversation.in_service_window(SessionLocal(), phone, utcnow())

    add_message(phone, "in", "82 kg", age=timedelta(hours=2))
    assert conversation.in_service_window(SessionLocal(), phone, utcnow())


def test_send_and_store_saves_the_message(monkeypatch):
    monkeypatch.setattr(conversation.whatsapp, "send_text", lambda *args: "wamid.out1")
    wamid = conversation.send_and_store(Settings(), "48111222333", "Hej!")
    assert wamid == "wamid.out1"
    history = conversation.recent_history(SessionLocal(), "48111222333", utcnow())
    assert [(m.direction, m.wamid, m.body) for m in history] == [("out", "wamid.out1", "Hej!")]


def test_reply_without_api_key_sends_apology(client, monkeypatch):
    sent = []
    monkeypatch.setattr(conversation.whatsapp, "send_text", lambda s, to, text: sent.append(text))
    monkeypatch.setattr(main, "get_settings", lambda: Settings(openai_api_key=""))

    post_signed(client, text_payload("wamid.1", "hej yanek"))
    assert sent == [main.APOLOGY]


def test_failed_delivery_is_logged(client, caplog):
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.x",
                                    "status": "failed",
                                    "errors": [{"code": 131047, "title": "Re-engagement message"}],
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    assert post_signed(client, payload).status_code == 200
    assert "131047" in caplog.text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Yanek, 82,4 dziś", True),
        ("hej yanku!", True),
        ("napisz do Yanka", True),
        ("@yanek trening A zrobiony", True),
        ("82,4 dziś", False),
        ("Janek był na siłowni", False),
        ("yaneks", False),
        (None, False),
    ],
)
def test_mentions_bot(text, expected):
    assert conversation.mentions_bot(text) is expected


def test_training_plan_is_injected_into_the_instructions():
    instructions = assistant.build_instructions("Dzień A – przysiad 4 x 6")
    assert instructions.startswith(assistant.PROMPTS["system"])
    assert "<plan_treningowy>\nDzień A – przysiad 4 x 6\n</plan_treningowy>" in instructions
    assert "{training_plan}" not in instructions


def test_instructions_without_a_plan_say_so():
    instructions = assistant.build_instructions(None, "TASK")
    assert assistant.PROMPTS["no_training_plan"] in instructions
    assert "<plan_treningowy>" not in instructions
    assert instructions.endswith("TASK")


def test_training_plan_comes_from_the_configured_file(tmp_path):
    plan = tmp_path / "plan.txt"
    plan.write_text("Dzień A – przysiad", encoding="utf-8")
    settings = Settings(training_plan_file=str(plan))
    assert conversation.training_plan_for(settings, "48111222333") == "Dzień A – przysiad"
    assert conversation.training_plan_for(Settings(training_plan_file=None), "48111222333") is None


def test_example_training_plan_exists():
    assert "Dzień A" in Path(Settings().training_plan_file).read_text(encoding="utf-8")


def test_exercise_catalog_is_in_the_instructions():
    instructions = assistant.build_instructions(None)
    assert "- leg_press: Suwnica na nogi (też: suwnica, wypychanie nóg, leg press) [nogi]" in (
        instructions
    )
    assert "{exercise_catalog}" not in instructions


def test_every_example_plan_exercise_is_in_the_catalog():
    plan = Path(Settings().training_plan_file).read_text(encoding="utf-8").casefold()
    names = [e.name.casefold() for e in exercises.CATALOG]
    missing = [
        line
        for line in plan.splitlines()
        if re.match(r"\d+\. ", line) and not any(name in line for name in names)
    ]
    assert missing == []
