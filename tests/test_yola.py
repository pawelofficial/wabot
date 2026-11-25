"""Yola: a trainee in YOLA_TRAINEE_IDS who mentions "yola" talks to plain GPT; nothing of it is
stored, so Yanek's history and replies don't change."""

# The fixtures shared with test_telegram.py are imported, then used as test arguments.
# ruff: noqa: F401, F811

import pytest

from wabot import yola
from wabot.config import Settings

from .test_telegram import (
    CHAT,
    connect,
    llm,
    post,
    settings_override,
    stored_messages,
    telegram_sent,
    update,
)
from .test_webhook import add_trainee


@pytest.fixture
def model(monkeypatch) -> list:
    """The model replaced: answers "odpowiedź N", recording the history and text it got."""
    calls = []

    def fake_ask(settings, history, text):
        calls.append((list(history), text))
        return f"odpowiedź {len(calls)}"

    monkeypatch.setattr(yola, "_ask", fake_ask)
    monkeypatch.setattr(yola, "_memory", {})
    return calls


@pytest.fixture
def no_wait(monkeypatch) -> list:
    """time.sleep recorded instead of waited."""
    waits = []
    monkeypatch.setattr(yola.time, "sleep", waits.append)
    return waits


@pytest.fixture
def yola_for(client, settings_override, telegram_sent):
    """A connected trainee, allowed to talk to Yola."""
    trainee = add_trainee()
    settings_override(Settings(yola_trainee_ids=f" 7, {trainee.id}"))
    connect(client, trainee)
    telegram_sent.clear()
    return trainee


def test_yola_answers_and_nothing_is_stored(client, yola_for, model, no_wait, telegram_sent, llm):
    before = len(stored_messages())
    post(client, update(10, "Yola, co sądzisz o kawie?"))
    assert model == [([], "Yola, co sądzisz o kawie?")]
    assert [(m.chat_id, m.text) for m in telegram_sent] == [(CHAT, "odpowiedź 1")]
    assert len(stored_messages()) == before  # the database never sees it
    assert llm == []  # Yanek wasn't asked


def test_she_remembers_the_conversation_until_reset(
    client, yola_for, model, no_wait, telegram_sent
):
    post(client, update(10, "yola cześć"))
    post(client, update(11, "a pamiętasz co pisałem, yolu?"))
    assert model[1][0] == [
        {"role": "user", "content": "yola cześć"},
        {"role": "assistant", "content": "odpowiedź 1"},
    ]
    post(client, update(12, "Yola reset!"))
    assert telegram_sent[-1].text == yola.FORGOT
    post(client, update(13, "yola hej"))
    assert model[-1] == ([], "yola hej")


def test_yanek_doesnt_see_yola(client, yola_for, model, no_wait, llm):
    post(client, update(10, "yola jak się masz"))
    post(client, update(11, "yanek waga 82"))
    [history] = llm
    assert history[-1] == "yanek waga 82"
    assert not any("yola" in (body or "") for body in history)  # Yola isn't in his history


def test_others_cant_talk_to_her(client, settings_override, model, telegram_sent, llm):
    trainee = add_trainee()
    settings_override(Settings(yola_trainee_ids=""))
    connect(client, trainee)
    post(client, update(10, "yola, yanek: waga 82"))
    assert model == []
    assert llm[-1][-1] == "yola, yanek: waga 82"  # Yanek's, as before
    assert "yola, yanek: waga 82" in [m.body for m in stored_messages()]


def test_yolo_isnt_her_name():
    assert not yola.calls_yola("yolo, robię dziś nogi")
    assert yola.calls_yola("YOLA?") and yola.calls_yola("napisz do yoli")


def test_the_answer_comes_after_the_delay(client, yola_for, settings_override, model, no_wait):
    settings_override(Settings(yola_trainee_ids=str(yola_for.id), yola_delay=30))
    post(client, update(10, "yola hej"))
    assert len(no_wait) == 1 and 29 < no_wait[0] <= 30  # what's left after the model's time


def test_a_failed_model_call_gets_an_apology(client, yola_for, monkeypatch, no_wait, telegram_sent):
    def broken(settings, history, text):
        raise RuntimeError("OpenAI down")

    monkeypatch.setattr(yola, "_ask", broken)
    post(client, update(10, "yola hej"))
    assert telegram_sent[-1].text == yola.FAILED


def test_a_long_answer_goes_in_pieces():
    pieces = yola._chunks(("a" * 3000 + "\n") * 3)
    assert [len(p) for p in pieces] == [3000, 3000, 3000]  # cut at the line breaks
    assert yola._chunks("b" * 5000) == ["b" * 4096, "b" * 904]  # no line break: cut hard
