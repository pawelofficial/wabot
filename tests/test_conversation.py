import datetime as dt
import runpy
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from wabot import assistant, conversation, exercises, main, plans, utils
from wabot.config import Settings
from wabot.db import SessionLocal
from wabot.models import DEFAULT_TIMEZONE, Message, Trainee, utcnow

from .test_webhook import PHONE, add_trainee, plan_with_day, post_signed, text_payload

EXAMPLE_PLAN = Path("plans/example.yaml")


def add_message(
    phone: str, direction: str, body: str, age: timedelta, trainee: Trainee | None = None
) -> Message:
    with SessionLocal() as session:
        msg = Message(
            direction=direction,
            phone=phone,
            type="text",
            body=body,
            created_at=utcnow() - age,
            trainee_id=trainee.id if trainee else None,
        )
        session.add(msg)
        session.commit()
        return msg


def unsaved_trainee(plan: dict | None = None) -> Trainee:
    return Trainee(phone=PHONE, training_plan=plan, timezone=DEFAULT_TIMEZONE)


def test_hardcoded_first_message_skips_the_llm(monkeypatch):
    def fail(*args):
        raise AssertionError("The LLM must not be called")

    monkeypatch.setattr(assistant, "generate_first_message", fail)
    settings = Settings(hardcoded_first_message=True)
    text = conversation.compose_first_message(settings, unsaved_trainee(), [], utcnow())
    assert text == utils.hardcoded_first_message()


def test_first_message_comes_from_the_llm_by_default(monkeypatch):
    monkeypatch.setattr(assistant, "generate_first_message", lambda *args: "Cześć od modelu")
    text = conversation.compose_first_message(Settings(), unsaved_trainee(), [], utcnow())
    assert text == "Cześć od modelu"


def test_first_message_without_api_key_raises():
    with pytest.raises(assistant.MissingApiKeyError):
        conversation.compose_first_message(
            Settings(openai_api_key=""), unsaved_trainee(), [], utcnow()
        )


def test_service_window_needs_a_recent_inbound_message():
    phone = "48111222333"
    add_message(phone, "in", "hej", age=timedelta(hours=30))
    add_message(phone, "out", "Cześć!", age=timedelta(hours=1))
    assert not conversation.in_service_window(SessionLocal(), phone, utcnow())

    add_message(phone, "in", "82 kg", age=timedelta(hours=2))
    assert conversation.in_service_window(SessionLocal(), phone, utcnow())


def test_send_and_store_saves_the_message(monkeypatch):
    trainee = add_trainee()
    monkeypatch.setattr(conversation.whatsapp, "send_text", lambda *args: "wamid.out1")
    wamid = conversation.send_and_store(Settings(), trainee, "Hej!")
    assert wamid == "wamid.out1"
    history = conversation.recent_history(SessionLocal(), trainee.id, utcnow())
    assert [(m.direction, m.wamid, m.body) for m in history] == [("out", "wamid.out1", "Hej!")]


def test_history_is_the_trainees_own(monkeypatch):
    ania, basia = add_trainee(), add_trainee(phone="48999888777")
    add_message(ania.phone, "in", "yanek 82", timedelta(minutes=5), ania)
    add_message(basia.phone, "in", "yanek 64", timedelta(minutes=4), basia)
    # From before the number was registered (or from its previous owner).
    add_message(ania.phone, "in", "stara wiadomość", timedelta(minutes=3))
    history = conversation.recent_history(SessionLocal(), ania.id, utcnow())
    assert [m.body for m in history] == ["yanek 82"]


@pytest.mark.parametrize(
    ("later", "expected"),
    [
        ([], False),
        ([("in", "a, i jeszcze 2 kg mniej")], False),
        ([("in", "yanek sorki, 81")], True),
        ([("in", "kg"), ("in", "yanku, zapisz")], True),
        # The bot wrote in between (e.g. a reminder): the later message starts a new report.
        ([("out", "Jak dziś trening?"), ("in", "yanek zrobiony")], False),
    ],
)
def test_asked_again(later, expected):
    trainee = add_trainee()
    first = add_message(PHONE, "in", "yanek 80", timedelta(minutes=5), trainee)
    for i, (direction, body) in enumerate(later):
        add_message(PHONE, direction, body, timedelta(minutes=4 - i), trainee)
    add_message("48999888777", "in", "yanek 64", timedelta(minutes=1))  # someone else
    assert conversation.asked_again(SessionLocal(), first) is expected


def test_reply_without_api_key_sends_apology(client, monkeypatch):
    sent = []
    monkeypatch.setattr(conversation.whatsapp, "send_text", lambda s, to, text: sent.append(text))
    monkeypatch.setattr(main, "get_settings", lambda: Settings(openai_api_key=""))
    add_trainee()

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
    plan = plans.TrainingPlan.model_validate(plan_with_day("Dzień A"))
    instructions = assistant.build_instructions(plan)
    assert instructions.startswith(assistant.PROMPTS["system"])
    assert f"<plan_treningowy>\n{plan.prompt_text()}\n</plan_treningowy>" in instructions
    assert "{training_plan}" not in instructions


def test_plan_text_shows_ids_aliases_and_replacements():
    plan = plans.TrainingPlan.model_validate(
        {
            "notes": "Cel: siła.",
            "days": [
                {
                    "name": "Dzień A",
                    "exercises": [
                        {
                            "id": "back_squat",
                            "sets": 4,
                            "reps": "6–8",
                            "weight": "ok. 60 kg",
                            "replacements": ["leg_press"],
                        },
                        {"id": "plank", "reps": "45 s"},
                    ],
                }
            ],
        }
    )
    assert plan.prompt_text() == (
        "Cel: siła.\n\nDzień A\n"
        "- back_squat: Przysiad ze sztangą (też: przysiad, przysiady, siady, squat, back squat)"
        " – 4 x 6–8, ok. 60 kg\n"
        "  zamienniki: leg_press: Suwnica na nogi (też: suwnica, wypychanie nóg, leg press)\n"
        "- plank: Plank (też: deska, planka) – 45 s"
    )


def test_trainee_name_is_in_the_instructions_after_the_shared_rules():
    instructions = assistant.build_instructions(None, "Kasia")
    name_line = "Podopieczny, z którym rozmawiasz, ma na imię Kasia."
    assert instructions.startswith(assistant.PROMPTS["system"])
    assert name_line in instructions
    assert "{trainee_name}" not in instructions
    assert name_line not in assistant.build_instructions(None)


def test_the_whole_catalog_is_not_in_the_instructions():
    plan = plans.TrainingPlan.model_validate(plan_with_day("Dzień A"))
    instructions = assistant.build_instructions(plan)
    assert "back_squat" in instructions
    assert "leg_press" not in instructions


@pytest.mark.parametrize(
    ("plan", "error"),
    [
        ({"days": [{"name": "A", "exercises": [{"id": "squat_jump"}]}]}, "squat_jump"),
        (
            {"days": [{"name": "A", "exercises": [{"id": "plank", "replacements": ["plank"]}]}]},
            "its own replacement",
        ),
        ({"days": [{"name": "A", "exercises": [{"id": "plank", "replacement": []}]}]}, "Extra"),
        ({"days": [{"name": "A", "exercises": []}]}, "at least 1"),
        (
            {
                "days": [
                    {"name": "A", "exercises": [{"id": "plank"}]},
                    {"name": "a", "exercises": [{"id": "plank"}]},
                ]
            },
            "same name",
        ),
    ],
)
def test_bad_plans_are_rejected(plan, error):
    with pytest.raises(ValidationError) as e:
        plans.TrainingPlan.model_validate(plan)
    assert error in str(e.value)


def test_instructions_without_a_plan_say_so():
    instructions = assistant.build_instructions(None, task="TASK")
    assert assistant.PROMPTS["no_training_plan"] in instructions
    assert "<plan_treningowy>" not in instructions
    assert instructions.endswith("TASK")


def test_example_training_plan_is_valid():
    # Loading checks every ID against the catalog.
    plan = plans.load(EXAMPLE_PLAN)
    assert [d.name[:7] for d in plan.days] == ["Dzień A", "Dzień B", "Dzień C"]


@pytest.mark.parametrize("path", sorted(Path("plans").glob("*.yaml")), ids=lambda p: p.stem)
def test_every_plan_file_is_valid(path):
    assert plans.load(path).days


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Siady", "back_squat"), ("  suwnica ", "leg_press"), ("Pompki", "push_up"), ("joga", None)],
)
def test_catalog_lookup_by_name_or_alias(name, expected):
    assert exercises.lookup(name) == expected


def chat(*turns: tuple[str, str]) -> list[Message]:
    return [Message(direction=d, phone="48111222333", type="text", body=b) for d, b in turns]


REPORTED = chat(
    ("in", "yanek waga 81,9"),
    ("out", "Zanotowałem 81,9 kg."),
    ("in", "przysiady 4x8"),
    ("in", "yanek zapisz"),
)


def is_marker(turn: dict) -> bool:
    return turn["role"] == "developer" and "NOWE WIADOMOŚCI" in turn["content"]


def test_marked_history_puts_the_marker_after_the_bots_last_message():
    turns = assistant.reply_input(Settings(), REPORTED)
    assert [t["content"] for t in turns if not is_marker(t)] == [m.body for m in REPORTED]
    assert is_marker(turns[2])
    assert sum(is_marker(t) for t in turns) == 1


def test_new_only_history_keeps_just_the_new_report():
    turns = assistant.reply_input(Settings(history_mode="new_only"), REPORTED)
    assert turns == [
        {"role": "user", "content": "przysiady 4x8"},
        {"role": "user", "content": "yanek zapisz"},
    ]


@pytest.mark.parametrize("mode", ["marked", "new_only"])
def test_without_a_bot_message_everything_is_new_and_unmarked(mode):
    history = chat(("in", "82"), ("in", "yanek zanotuj"))
    turns = assistant.reply_input(Settings(history_mode=mode), history)
    assert [t["content"] for t in turns] == ["82", "yanek zanotuj"]


def test_reply_call_sends_the_marked_history(monkeypatch):
    sent = {}

    def fake_complete(settings, instructions, messages, today, training_plan):
        sent["messages"] = messages
        return None

    monkeypatch.setattr(assistant, "_complete", fake_complete)
    assistant.generate_reply(Settings(), unsaved_trainee(), REPORTED, utcnow())
    assert is_marker(sent["messages"][2])
    assert sent["messages"][-1]["content"].startswith("Dzisiaj jest")


def test_today_is_the_trainees_local_date(monkeypatch):
    sent = {}

    def fake_complete(settings, instructions, messages, today, training_plan):
        sent.update(today=today, last=messages[-1]["content"])

    monkeypatch.setattr(assistant, "_complete", fake_complete)
    late_evening_utc = dt.datetime(2026, 11, 3, 23, 30, tzinfo=dt.UTC)
    assistant.generate_reply(Settings(), unsaved_trainee(), REPORTED, late_evening_utc)
    # Already Wednesday in Warsaw.
    assert sent["today"] == dt.date(2026, 11, 4)
    assert sent["last"] == "Dzisiaj jest środa, 2026-11-04, godz. 00:30."


def run_add_trainee(monkeypatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["add_trainee.py", *args])
    runpy.run_path("scripts/add_trainee.py", run_name="__main__")


def test_add_trainee_registers_and_then_updates(monkeypatch, capsys):
    run_add_trainee(monkeypatch, "--phone", "+48 111 222 333", "--name", "Ania")
    with SessionLocal() as session:
        trainee = session.scalar(select(Trainee))
        assert (trainee.phone, trainee.name, trainee.trainer.name) == (
            PHONE,
            "Ania",
            "Trener testowy",
        )
        assert plans.of(trainee) == plans.load(EXAMPLE_PLAN)
        assert trainee.consent_date == dt.date.today()

    run_add_trainee(monkeypatch, "--phone", PHONE, "--no-plan", "--trainer", "Tomek")
    with SessionLocal() as session:
        trainee = session.scalar(select(Trainee))
        # Only what was passed changes.
        assert (trainee.name, trainee.training_plan, trainee.trainer.name) == (
            "Ania",
            None,
            "Tomek",
        )
    assert "Updated Ania (48111222333), trainer Tomek, plan: none" in capsys.readouterr().out
