import datetime as dt
import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from wabot import assistant, entries, main, plan_history, plans, trainees, whatsapp
from wabot.config import Settings
from wabot.db import SessionLocal
from wabot.llm_output import Exercise, LlmOutput, Training, Weight
from wabot.models import Message, Trainee, TrainingCheckin, WeightEntry, utcnow
from wabot.plans import TrainingPlan

PHONE = "48111222333"


def plan_with_day(name: str) -> dict:
    """A one-day plan as stored in a plan version (trainee_plans.plan)."""
    return {"days": [{"name": name, "exercises": [{"id": "back_squat", "sets": 4, "reps": "6"}]}]}


PLAN = plan_with_day("Dzień A")


def add_trainee(phone: str = PHONE, plan: dict | None = PLAN, trainer: str = "Trener") -> Trainee:
    with SessionLocal() as session:
        trainee = Trainee(
            trainer=trainees.trainer_named(session, trainer),
            phone=phone,
            name="Ania",
            consent_date=dt.date(2026, 11, 1),
        )
        if plan is not None:
            plan_history.assign(session, trainee, TrainingPlan.model_validate(plan), "script")
        session.add(trainee)
        session.commit()
        return trainee


@pytest.fixture
def trainee() -> Trainee:
    """A registered trainee with the number the payloads come from."""
    return add_trainee()


def text_payload(wamid: str, text: str, phone: str = PHONE) -> dict:
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
    """Replace the LLM with a stub; records the history it was given (context and new)."""
    histories = []

    def fake_generate_reply(settings, trainee, context, new, now):
        histories.append(context + new)
        return LlmOutput(reply=f"Odpowiedź {len(histories)}", weight=None, training=None)

    monkeypatch.setattr(assistant, "generate_reply", fake_generate_reply)
    return histories


@pytest.fixture
def llm_returns(monkeypatch):
    """Replace the LLM with a stub that returns the given outputs, one per call."""

    def install(*outputs: LlmOutput):
        queue = list(outputs)
        monkeypatch.setattr(assistant, "generate_reply", lambda *args: queue.pop(0))

    return install


def stored_messages() -> list[Message]:
    with SessionLocal() as session:
        return list(session.scalars(select(Message).order_by(Message.id)))


def stored_weights() -> list[WeightEntry]:
    with SessionLocal() as session:
        return list(session.scalars(select(WeightEntry).order_by(WeightEntry.id)))


def weight_output(kg: float, reply: str = "Zapisałem.") -> LlmOutput:
    return LlmOutput(reply=reply, weight=Weight(kg=kg, date=utcnow().date()), training=None)


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


def test_replies_with_the_llm_and_stores_both_messages(client, sent, llm, trainee):
    response = post_signed(client, text_payload("wamid.1", "yanek 82,4 rano"))
    assert response.status_code == 200
    assert sent == [("48111222333", "Odpowiedź 1")]

    inbound, outbound = stored_messages()
    assert (inbound.direction, inbound.external_id, inbound.body) == (
        "in",
        "wamid.1",
        "yanek 82,4 rano",
    )
    assert (outbound.direction, outbound.external_id) == ("out", "wamid.out1")
    assert inbound.created_at.tzinfo is not None
    for msg in (inbound, outbound):
        assert (msg.trainee_id, msg.trainer_id) == (trainee.id, trainee.trainer_id)


def test_duplicate_delivery_is_ignored(client, sent, llm, trainee):
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


def test_history_includes_previous_turns_of_the_same_trainee(client, sent, llm, trainee):
    add_trainee(phone="48999888777")
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


def test_messages_without_the_bot_name_are_stored_but_not_answered(client, sent, llm, trainee):
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
        Message(direction="in", phone="1", type="image", body="yanek 80,4"),  # with a caption
    ]
    assert assistant.to_llm_messages(history) == [
        {
            "role": "developer",
            "content": assistant.PROMPTS["earlier_bot_messages"].strip()
            + "\n[wiadomość typu template]",
        },
        {"role": "user", "content": "hej"},
        {"role": "assistant", "content": "Cześć!"},
        {"role": "user", "content": "[wiadomość typu image]"},
        {"role": "user", "content": "[wiadomość typu image] yanek 80,4"},
    ]


def test_an_image_caption_is_the_message_text(client, trainee, sent, llm):
    payload = text_payload("wamid.1", "")
    [msg] = payload["entry"][0]["changes"][0]["value"]["messages"]
    del msg["text"]
    msg["type"] = "image"
    msg["image"] = {"id": "media-1", "mime_type": "image/jpeg", "caption": "yanek waga 80,4"}
    post_signed(client, payload)
    with SessionLocal() as session:
        stored = session.scalars(select(Message).where(Message.direction == "in")).one()
    assert (stored.type, stored.body) == ("image", "yanek waga 80,4")
    assert len(llm) == 1 and len(sent) == 1


def test_a_stored_plan_that_no_longer_validates_gets_the_apology(trainee, sent, monkeypatch):
    # E.g. an exercise taken out of the catalog: the trainee is told, not left without a reply.
    with SessionLocal() as session:
        version = session.get(Trainee, trainee.id).plan_version
        version.plan = {"days": [{"name": "A", "exercises": [{"id": "not_in_the_catalog"}]}]}
        message = Message(
            channel="whatsapp",
            external_id="wamid.1",
            direction="in",
            phone=PHONE,
            type="text",
            body="yanek waga 80",
            trainee_id=trainee.id,
            trainer_id=trainee.trainer_id,
        )
        session.add(message)
        session.commit()
        message_id = message.id
    main.reply(message_id)
    assert sent == [(PHONE, main.APOLOGY)]


def test_unregistered_number_gets_a_fixed_reply_and_its_text_is_not_kept(client, sent, llm):
    post_signed(client, text_payload("wamid.1", "yanek, waga 82"))
    post_signed(client, text_payload("wamid.1", "yanek, waga 82"))  # redelivered

    assert sent == [(PHONE, main.UNREGISTERED)]
    assert llm == []
    [stored] = stored_messages()
    assert (stored.external_id, stored.body, stored.trainee_id) == ("wamid.1", None, None)


def test_the_trainees_own_plan_goes_to_the_llm(client, sent, monkeypatch):
    add_trainee(plan=plan_with_day("Plan Ani"))
    add_trainee(phone="48999888777", plan=plan_with_day("Plan Basi"))
    days = []

    def fake_generate_reply(settings, trainee, context, new, now):
        days.append(plans.of(trainee).days[0].name)
        return LlmOutput(reply="Ok.", weight=None, training=None)

    monkeypatch.setattr(assistant, "generate_reply", fake_generate_reply)
    post_signed(client, text_payload("wamid.1", "yanek, co mam dziś?", phone="48999888777"))
    assert days == ["Plan Basi"]


def test_reported_weight_is_saved(client, sent, trainee, llm_returns):
    llm_returns(weight_output(80.0, reply="Zapisałem 80 kg."))
    post_signed(client, text_payload("wamid.1", "yanek waga 80"))

    [entry] = stored_weights()
    [inbound, _] = stored_messages()
    assert (entry.kg, entry.date, entry.message_id) == (80.0, utcnow().date(), inbound.id)
    assert (entry.trainee_id, entry.trainer_id) == (trainee.id, trainee.trainer_id)
    assert sent == [(PHONE, "Zapisałem 80 kg.")]


@pytest.mark.parametrize("daily_updates_only", [False, True])
def test_a_correction_is_saved(client, sent, trainee, llm_returns, monkeypatch, daily_updates_only):
    settings = Settings(daily_updates_only=daily_updates_only)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    llm_returns(weight_output(80.0), weight_output(81.0))
    post_signed(client, text_payload("wamid.1", "yanek zanotuj 80kg"))
    post_signed(client, text_payload("wamid.2", "yanek sorki, zanotuj 81kg"))

    saved = stored_weights()
    correction = next(m for m in stored_messages() if m.external_id == "wamid.2")
    # The newest row of the day is the current value either way.
    assert (saved[-1].kg, saved[-1].message_id) == (81.0, correction.id)
    # By default the first report is kept too.
    assert [e.kg for e in saved] == ([81.0] if daily_updates_only else [80.0, 81.0])


def test_a_failed_save_sends_the_apology_instead_of_the_confirmation(
    client, sent, trainee, llm_returns, monkeypatch
):
    def fail(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(entries, "save", fail)
    llm_returns(weight_output(80.0, reply="Zapisałem 80 kg."))
    post_signed(client, text_payload("wamid.1", "yanek waga 80"))
    assert sent == [(PHONE, main.APOLOGY)]
    # Nothing saved, so the message still waits for the next "yanek".
    [inbound, _] = stored_messages()
    assert inbound.handled_at is None


@pytest.fixture
def llm_turns(monkeypatch):
    """Replace the LLM with answers given in order (an exception is raised); records what each
    call got as (context, new) message texts. `on_call(n)` runs during the n-th call."""

    def install(*answers, on_call=lambda n: None) -> list[tuple[list, list]]:
        queue, turns = list(answers), []

        def fake_generate_reply(settings, trainee, context, new, now):
            turns.append(([m.body for m in context], [m.body for m in new]))
            on_call(len(turns))
            answer = queue.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(assistant, "generate_reply", fake_generate_reply)
        return turns

    return install


def nothing_to_save(reply: str = "Ok.") -> LlmOutput:
    return LlmOutput(reply=reply, weight=None, training=None)


def test_after_a_failed_send_the_report_isnt_saved_again(client, trainee, llm_turns, monkeypatch):
    # The reply never reached the trainee, but the weight was saved: the next "yanek" reads the
    # report as context, not as new (before 2026-11-13 it was saved a second time).
    sent = []

    def send_text(settings, to, text):
        if not sent:
            sent.append(None)
            raise RuntimeError("Meta is down")
        sent.append(text)
        return "wamid.out"

    monkeypatch.setattr(whatsapp, "send_text", send_text)
    turns = llm_turns(weight_output(80.0), nothing_to_save("Jutro dzień B."))
    post_signed(client, text_payload("wamid.1", "yanek waga 80"))
    post_signed(client, text_payload("wamid.2", "yanek a co jutro?"))
    assert turns[1] == (["yanek waga 80"], ["yanek a co jutro?"])
    assert [e.kg for e in stored_weights()] == [80.0]
    assert sent == [None, "Jutro dzień B."]


def test_after_an_apology_the_report_is_read_again(client, sent, trainee, llm_turns):
    # The LLM call failed: the trainee got the apology and the report keeps waiting, so
    # "yanek, spróbuj jeszcze raz" saves it (before 2026-11-13 the apology made it count as
    # saved, and it was lost).
    turns = llm_turns(RuntimeError("OpenAI is down"), weight_output(80.0, reply="Zapisałem 80."))
    post_signed(client, text_payload("wamid.1", "yanek waga 80"))
    post_signed(client, text_payload("wamid.2", "yanek, spróbuj jeszcze raz"))
    assert turns[1] == ([], ["yanek waga 80", main.APOLOGY, "yanek, spróbuj jeszcze raz"])
    assert [e.kg for e in stored_weights()] == [80.0]
    assert sent == [(PHONE, main.APOLOGY), (PHONE, "Zapisałem 80.")]
    assert all(m.handled_at for m in stored_messages() if m.direction == "in")


def test_messages_another_reply_took_meanwhile_are_read_again(client, sent, trainee, llm_turns):
    # Two replies at once: while this one waits for the LLM, another one saves "waga 80" (it was
    # waiting for both). This answer is out of date, so the reply reads the messages again.
    post_signed(client, text_payload("wamid.1", "waga 80"))

    def other_reply_saves_the_weight(n):
        if n == 1:
            with SessionLocal() as session:
                first = session.scalars(select(Message).where(Message.body == "waga 80")).one()
                first.handled_at = utcnow()
                session.commit()

    turns = llm_turns(
        weight_output(80.0), nothing_to_save("Ok."), on_call=other_reply_saves_the_weight
    )
    post_signed(client, text_payload("wamid.2", "yanek zapisz"))
    assert turns == [([], ["waga 80", "yanek zapisz"]), (["waga 80"], ["yanek zapisz"])]
    assert stored_weights() == []  # the first answer, out of date, saved nothing
    assert sent == [(PHONE, "Ok.")]


def test_a_message_the_trainee_followed_up_on_is_not_answered(sent, trainee, llm_returns):
    # Both arrived before the first reply was ready; the second one's reply covers both, and the
    # first one doesn't even call the LLM.
    with SessionLocal() as session:
        first, second = (
            Message(
                channel="whatsapp",
                direction="in",
                phone=PHONE,
                type="text",
                body=body,
                trainee_id=trainee.id,
            )
            for body in ("yanek 80", "yanek sorki, 81")
        )
        session.add_all([first, second])
        session.commit()
    llm_returns(weight_output(81.0))

    main.reply(first.id)
    assert sent == []
    assert stored_weights() == []

    main.reply(second.id)
    assert [e.kg for e in stored_weights()] == [81.0]
    assert len(sent) == 1


def test_a_checkin_keeps_the_plan_version_the_reply_was_made_with(
    client, sent, monkeypatch, trainee
):
    """The trainer changes the plan while the LLM is answering: the check-in records the
    version the report was checked against, not the newer one."""

    def answer_while_the_plan_changes(settings, current, context, new, now):
        with SessionLocal() as session:
            plan_history.assign(
                session,
                session.get(Trainee, current.id),
                TrainingPlan.model_validate(plan_with_day("Dzień B")),
                "edited",
            )
            session.commit()
        squat = Exercise(
            exercise_id="back_squat",
            name="Przysiad ze sztangą",
            in_plan=True,
            sets=4,
            reps="6",
            weight_kg=None,
            replaces=None,
            comment=None,
        )
        training = Training(
            date=utcnow().date(), plan_day="Dzień A", status="done", exercises=[squat], notes=None
        )
        return LlmOutput(reply="Zapisałem Dzień A.", weight=None, training=training)

    monkeypatch.setattr(assistant, "generate_reply", answer_while_the_plan_changes)
    post_signed(client, text_payload("wamid.1", "yanek dzień A zrobiony"))
    with SessionLocal() as session:
        [checkin] = session.scalars(select(TrainingCheckin)).all()
        now_current = session.get(Trainee, trainee.id).plan_version_id
    assert checkin.plan_version_id == trainee.plan_version_id
    assert now_current != trainee.plan_version_id
