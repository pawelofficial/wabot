"""The Telegram channel: the webhook, connecting a trainee by their shared number, replies going
back where a message came from, and the Bot API client never leaking the bot token."""

import logging
import runpy
import sys
import time
import traceback
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from wabot import assistant, main, telegram
from wabot.config import Settings, get_settings
from wabot.db import SessionLocal
from wabot.llm_output import LlmOutput
from wabot.models import Message, Trainee, WeightEntry

from .conftest import REAL_TELEGRAM_CALL
from .test_webhook import PHONE, add_trainee, post_signed, text_payload, weight_output

CHAT = 555001
TOKEN = "123456:test-telegram-token"  # tests/conftest.py


def update(message_id: int, text: str | None = None, chat_id: int = CHAT, **fields) -> dict:
    """A webhook update with one message, as Telegram sends it."""
    message = {
        "message_id": message_id,
        "date": 1761739200,
        "chat": {"id": chat_id, "type": fields.pop("chat_type", "private")},
        "from": {"id": fields.pop("user_id", chat_id), "is_bot": False, "first_name": "Ania"},
        **fields,
    }
    if text is not None:
        message["text"] = text
    return {"update_id": 1000 + message_id, "message": message}


def contact(message_id: int, phone: str, user_id: int = CHAT, chat_id: int = CHAT) -> dict:
    """The trainee pressed "Udostępnij numer telefonu" (user_id: whose number it is)."""
    card = {"phone_number": phone, "first_name": "Ania", "user_id": user_id}
    return update(message_id, chat_id=chat_id, contact=card)


def post(client, payload: dict, secret: str | None = None):
    secret = telegram.webhook_secret(Settings()) if secret is None else secret
    return client.post("/telegram/webhook", json=payload, headers={telegram.SECRET_HEADER: secret})


@pytest.fixture
def telegram_sent(monkeypatch) -> list[SimpleNamespace]:
    """Capture what the bot sends on Telegram instead of calling it."""
    calls = []

    def fake_send_text(settings, chat_id, text, keyboard=None, markdown=False):
        calls.append(
            SimpleNamespace(chat_id=chat_id, text=text, keyboard=keyboard, markdown=markdown)
        )
        return f"tg:{chat_id}:{900 + len(calls)}"

    monkeypatch.setattr(telegram, "send_text", fake_send_text)
    return calls


@pytest.fixture
def llm(monkeypatch) -> list:
    """The LLM replaced: answers with weight 82, recording the history it was given."""
    histories = []

    def fake_generate_reply(settings, trainee, context, new, now):
        histories.append([m.body for m in context + new])
        return weight_output(82, reply="Zapisałem 82 kg.")

    monkeypatch.setattr(assistant, "generate_reply", fake_generate_reply)
    return histories


@pytest.fixture
def settings_override():
    """Run the app with other settings: settings_override(Settings(...))."""

    def use(settings: Settings) -> None:
        main.app.dependency_overrides[get_settings] = lambda: settings

    yield use
    main.app.dependency_overrides.pop(get_settings, None)


def stored_messages() -> list[Message]:
    with SessionLocal() as session:
        return list(session.scalars(select(Message).order_by(Message.id)))


def trainee_chat(trainee_id: int) -> int | None:
    with SessionLocal() as session:
        return session.get(Trainee, trainee_id).telegram_chat_id


def connect(client, trainee: Trainee) -> None:
    post(client, contact(1, f"+{trainee.phone}"))


# The webhook


@pytest.mark.parametrize("secret", ["", "wrong"])
def test_updates_without_the_secret_are_refused(client, telegram_sent, secret):
    assert post(client, update(1, "yanek 82"), secret=secret).status_code == 401
    assert client.post("/telegram/webhook", json=update(1, "yanek 82")).status_code == 401
    assert stored_messages() == [] and telegram_sent == []


def test_without_a_bot_token_every_update_is_refused(client, telegram_sent, settings_override):
    settings_override(Settings(telegram_bot_token=""))
    # Without a token the secret would be empty; an empty header mustn't match it.
    assert post(client, update(1, "yanek 82"), secret="").status_code == 401
    assert stored_messages() == []


def test_a_new_chat_is_asked_for_the_number(client, telegram_sent):
    add_trainee()
    assert post(client, update(1, "/start")).status_code == 200
    [stored] = stored_messages()
    # Someone the bot doesn't know yet: only what dedup needs.
    assert (stored.channel, stored.external_id, stored.direction) == (
        "telegram",
        "tg:555001:1",
        "in",
    )
    assert (stored.body, stored.phone, stored.trainee_id) == (None, None, None)
    [sent] = telegram_sent
    assert (sent.chat_id, sent.text, sent.keyboard) == (
        CHAT,
        main.TELEGRAM_ASK_FOR_PHONE,
        telegram.ASK_FOR_PHONE,
    )
    assert sent.keyboard["keyboard"][0][0]["request_contact"] is True


def test_sharing_ones_number_connects_the_trainee(client, telegram_sent, llm):
    trainee = add_trainee()
    post(client, contact(1, "+48 111 222 333"))  # Telegram may send it with a "+"
    assert trainee_chat(trainee.id) == CHAT
    [sent] = telegram_sent
    assert (sent.text, sent.keyboard) == (main.TELEGRAM_LINKED, telegram.REMOVE_KEYBOARD)
    shared, welcome = stored_messages()
    assert (shared.type, shared.trainee_id, shared.phone) == ("contact", trainee.id, PHONE)
    # The welcome is history: the model knows what the bot told the trainee.
    assert (welcome.direction, welcome.body, welcome.external_id) == (
        "out",
        main.TELEGRAM_LINKED,
        "tg:555001:901",
    )

    # From now on it's the same as on WhatsApp: stored, and answered when called by name.
    post(client, update(2, "rano 82"))
    post(client, update(3, "yanek zanotuj"))
    assert llm == [[None, main.TELEGRAM_LINKED, "rano 82", "yanek zanotuj"]]
    assert [(s.chat_id, s.text, s.keyboard) for s in telegram_sent[1:]] == [
        (CHAT, "Zapisałem 82 kg.", None)
    ]
    reply = stored_messages()[-1]
    assert (reply.channel, reply.direction, reply.external_id, reply.body) == (
        "telegram",
        "out",
        "tg:555001:902",
        "Zapisałem 82 kg.",
    )
    with SessionLocal() as session:
        assert session.scalars(select(WeightEntry.kg)).all() == [82]


def test_start_again_shows_how_to_report(client, telegram_sent, llm):
    connect(client, add_trainee())
    post(client, update(2, "/start"))
    assert telegram_sent[-1].text == main.TELEGRAM_HELP
    assert stored_messages()[-1].body == main.TELEGRAM_HELP
    assert llm == []


def test_someone_elses_contact_card_connects_nobody(client, telegram_sent):
    trainee = add_trainee()
    post(client, contact(1, PHONE, user_id=777))  # a contact from the address book
    assert trainee_chat(trainee.id) is None
    assert telegram_sent[0].text == main.TELEGRAM_ASK_FOR_PHONE
    # Nothing kept but what dedup needs; the bot's answer isn't stored either.
    assert [(m.direction, m.trainee_id) for m in stored_messages()] == [("in", None)]


def test_a_number_that_isnt_registered_is_told_to_ask_the_trainer(client, telegram_sent):
    add_trainee()
    post(client, contact(1, "48999888777"))
    [sent] = telegram_sent
    # The button stays: once the trainer registers the number, sharing it again works.
    assert (sent.text, sent.keyboard) == (main.UNREGISTERED, telegram.ASK_FOR_PHONE)
    assert stored_messages()[0].trainee_id is None


def test_a_trainee_on_a_new_telegram_account_moves_there(client, telegram_sent):
    trainee = add_trainee()
    connect(client, trainee)
    post(client, contact(1, PHONE, user_id=888, chat_id=888))
    assert trainee_chat(trainee.id) == 888
    post(client, update(2, "yanek 82"))  # the old chat is nobody's now
    assert telegram_sent[-1].text == main.TELEGRAM_ASK_FOR_PHONE


def test_a_redelivered_update_is_answered_once(client, telegram_sent, llm):
    connect(client, add_trainee())
    post(client, update(2, "yanek 82"))
    post(client, update(2, "yanek 82"))
    assert len(llm) == 1
    assert [m.external_id for m in stored_messages() if m.direction == "in"] == [
        "tg:555001:1",
        "tg:555001:2",
    ]


def test_groups_and_other_updates_are_ignored(client, telegram_sent):
    connect(client, add_trainee())
    post(client, update(2, "yanek 82", chat_id=-100123, user_id=CHAT, chat_type="group"))
    post(client, {"update_id": 9, "edited_message": update(3, "yanek 81")["message"]})
    assert len(stored_messages()) == 2 and len(telegram_sent) == 1  # just the connecting


def test_start_and_the_shared_number_dont_hide_a_waiting_report(client, telegram_sent, monkeypatch):
    # The help after /start is a bot message, but not a reply: "waga 80,4" before it still
    # waits for the next "yanek" (before 2026-11-13 it counted as saved, and was lost).
    turns = []

    def fake_generate_reply(settings, trainee, context, new, now):
        turns.append(([m.body for m in context], [m.body for m in new]))
        return LlmOutput(reply="Zapisałem 80,4 kg.", weight=None, training=None)

    monkeypatch.setattr(assistant, "generate_reply", fake_generate_reply)
    connect(client, add_trainee())
    post(client, update(2, "waga 80,4"))
    post(client, update(3, "/start"))
    post(client, update(4, "yanek zapisz"))
    # The shared number (handled when the bot answered it) and the welcome are context.
    assert turns == [
        (
            [None, main.TELEGRAM_LINKED],
            ["waga 80,4", "/start", main.TELEGRAM_HELP, "yanek zapisz"],
        )
    ]


def test_a_photo_is_stored_without_text(client, telegram_sent, llm):
    connect(client, add_trainee())
    post(client, update(2, photo=[{"file_id": "x", "width": 1, "height": 1}]))
    stored = stored_messages()[-1]
    assert (stored.type, stored.body) == ("photo", None)
    assert llm == []


def test_a_photo_caption_is_the_message_text(client, telegram_sent, llm):
    # A photo of the scale with "yanek 80,4" under it is a report like a text message.
    connect(client, add_trainee())
    photo = [{"file_id": "x", "width": 1, "height": 1}]
    post(client, update(2, photo=photo, caption="yanek waga 80,4"))
    photo_message = stored_messages()[-2]  # the last one is the reply
    assert (photo_message.type, photo_message.body) == ("photo", "yanek waga 80,4")
    assert llm[0][-1] == "yanek waga 80,4"
    assert telegram_sent[-1].text == "Zapisałem 82 kg."


def test_one_history_across_both_channels(client, telegram_sent, llm, monkeypatch):
    trainee = add_trainee()
    connect(client, trainee)
    whatsapp_sent = []
    monkeypatch.setattr(
        main.whatsapp, "send_text", lambda s, to, text: whatsapp_sent.append(text) or "wamid.o"
    )
    post_signed(client, text_payload("wamid.1", "rano 82"))  # on WhatsApp, not answered
    post(client, update(2, "yanek zanotuj"))  # on Telegram
    assert llm == [[None, main.TELEGRAM_LINKED, "rano 82", "yanek zanotuj"]]
    # The reply goes back where the message it answers came from.
    assert telegram_sent[-1].text == "Zapisałem 82 kg." and whatsapp_sent == []
    post_signed(client, text_payload("wamid.2", "yanek a teraz?"))
    assert whatsapp_sent == ["Zapisałem 82 kg."]
    assert [m.channel for m in stored_messages()] == [
        "telegram",  # the shared number
        "telegram",  # the welcome
        "whatsapp",
        "telegram",
        "telegram",
        "whatsapp",
        "whatsapp",
    ]


def test_a_failed_reply_is_logged(client, monkeypatch, caplog, llm):
    connect_sent = []
    monkeypatch.setattr(
        telegram, "send_text", lambda *args: connect_sent.append(args) or "tg:555001:900"
    )
    connect(client, add_trainee())

    def blocked(settings, chat_id, text, keyboard=None, markdown=False):
        raise telegram.TelegramError("sendMessage: 403 Forbidden: bot was blocked by the user")

    monkeypatch.setattr(telegram, "send_text", blocked)
    with caplog.at_level(logging.ERROR):
        post(client, update(2, "yanek 82"))
    assert "bot was blocked by the user" in caplog.text
    # The number, the welcome, the report; no reply.
    assert [m.direction for m in stored_messages()] == ["in", "out", "in"]


# The Bot API client


def fake_post(monkeypatch, respond) -> list:
    """httpx.post replaced: respond(url, payload) returns the response (or raises)."""
    requests = []

    def post_(url, json=None, timeout=None):
        requests.append((url, json))
        return respond(url, json)

    monkeypatch.setattr(telegram, "_call", REAL_TELEGRAM_CALL)
    monkeypatch.setattr(telegram.httpx, "post", post_)
    return requests


def test_sending_a_message(monkeypatch):
    requests = fake_post(
        monkeypatch,
        lambda url, payload: httpx.Response(200, json={"ok": True, "result": {"message_id": 77}}),
    )
    assert telegram.send_text(Settings(), 42, "Hej", telegram.ASK_FOR_PHONE) == "tg:42:77"
    [(url, payload)] = requests
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert payload == {"chat_id": 42, "text": "Hej", "reply_markup": telegram.ASK_FOR_PHONE}


def failure(kind: str):
    def respond(url, payload):
        if kind == "refused":
            body = {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}
            return httpx.Response(403, json=body)
        raise httpx.ConnectError(f"can't reach {url}", request=httpx.Request("POST", url))

    return respond


@pytest.mark.parametrize("kind", ["refused", "unreachable"])
def test_an_api_error_never_shows_the_token(monkeypatch, kind):
    fake_post(monkeypatch, failure(kind))
    with pytest.raises(telegram.TelegramError) as error:
        telegram.send_text(Settings(), 42, "Hej")
    shown = "".join(traceback.format_exception(error.value))  # what log.exception prints
    assert TOKEN not in shown
    assert str(error.value) in (
        "sendMessage: 403 Forbidden: bot was blocked",
        "sendMessage: ConnectError",
    )


def test_httpx_doesnt_log_request_urls():
    # Its INFO lines print the URL, which has the bot token.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_without_a_token_nothing_is_called():
    with pytest.raises(telegram.TelegramError, match="TELEGRAM_BOT_TOKEN is not set"):
        REAL_TELEGRAM_CALL(Settings(telegram_bot_token=""), "getMe")


def test_the_webhook_secret_comes_from_the_token():
    secret = telegram.webhook_secret(Settings())
    assert secret == telegram.webhook_secret(Settings())
    assert secret != telegram.webhook_secret(Settings(telegram_bot_token="999:other"))
    assert telegram.webhook_secret(Settings(telegram_bot_token="")) == ""
    # What Telegram accepts as a secret_token: 1-256 of A-Z, a-z, 0-9, _ and -.
    assert 1 <= len(secret) <= 256 and secret.isalnum()
    assert TOKEN not in secret


def test_parsing_updates():
    shared = telegram.parse_update(contact(5, "48111222333", user_id=9, chat_id=9))
    assert (shared.type, shared.text, shared.contact_phone, shared.contact_user_id) == (
        "contact",
        None,
        "48111222333",
        9,
    )
    sticker = telegram.parse_update(update(6, sticker={"file_id": "x"}))
    assert (sticker.type, sticker.text, sticker.private) == ("sticker", None, True)
    assert telegram.parse_update({"update_id": 1, "my_chat_member": {}}) is None


# Settings and the scripts


def test_only_the_active_channels_settings_are_needed():
    empty = dict(telegram_bot_token="", whatsapp_token="", whatsapp_phone_number_id="")
    assert Settings(**empty).missing_for_channel() == ["TELEGRAM_BOT_TOKEN"]
    assert Settings(**empty, channel="whatsapp").missing_for_channel() == [
        "WHATSAPP_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
    ]
    assert Settings().missing_for_channel() == []


def test_set_webhook_registers_the_telegram_url(monkeypatch, capsys):
    calls = []

    def public_dns(url, params, timeout):
        # The first time the name isn't there yet; Telegram is asked only once it is.
        calls.append(f"dns {params['name']}")
        found = calls.count(f"dns {params['name']}") > 1
        return httpx.Response(200, json={"Status": 0, "Answer": [{}]} if found else {"Status": 3})

    monkeypatch.setattr(httpx, "get", public_dns)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(telegram, "set_webhook", lambda s, url: calls.append(url))
    monkeypatch.setattr(telegram, "bot_username", lambda s: "YanekTestBot")
    monkeypatch.setattr(sys, "argv", ["set_webhook.py", "https://abc.trycloudflare.com/"])
    runpy.run_path("scripts/set_webhook.py", run_name="__main__")
    assert calls == [
        "dns abc.trycloudflare.com",
        "dns abc.trycloudflare.com",
        "https://abc.trycloudflare.com/telegram/webhook",
    ]
    assert "https://t.me/YanekTestBot" in capsys.readouterr().out


# WhatsApp without its settings


def test_whatsapp_refuses_everything_without_its_secrets(client, settings_override):
    settings_override(Settings(whatsapp_app_secret="", whatsapp_verify_token=""))
    # Signed with the empty secret, as anyone could.
    assert post_signed(client, text_payload("wamid.1", "yanek 82"), secret="").status_code == 401
    params = {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "42"}
    assert client.get("/webhook", params=params).status_code == 403


def test_replies_go_out_with_chat_formatting(client, telegram_sent, monkeypatch):
    monkeypatch.setattr(
        assistant,
        "generate_reply",
        lambda *args: LlmOutput(reply="Zapisałem *82 kg*.", weight=None, training=None),
    )
    connect(client, add_trainee())
    post(client, update(2, "yanek 82"))
    assert (telegram_sent[-1].text, telegram_sent[-1].markdown) == ("Zapisałem *82 kg*.", True)
    # The fixed texts have no formatting.
    assert telegram_sent[0].markdown is False


def test_text_telegram_cant_format_goes_out_as_it_is(monkeypatch):
    def respond(url, payload):
        if payload.get("parse_mode") == "Markdown":
            body = {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: can't parse entities: Can't find end of the entity",
            }
            return httpx.Response(400, json=body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 8}})

    requests = fake_post(monkeypatch, respond)
    assert telegram.send_text(Settings(), 42, "Przysiad 4 x 8_10", markdown=True) == "tg:42:8"
    assert [payload.get("parse_mode") for _, payload in requests] == ["Markdown", None]
