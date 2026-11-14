"""Dodaj podopiecznego: the trainer registers a trainee (name, phone, consent date, optionally a
library plan), and the trainee then connects on Telegram by sharing that number."""

# The fixtures shared with test_dashboard.py and test_telegram.py are imported, then used as
# test arguments.
# ruff: noqa: F401, F811

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select

from wabot import plan_history, plans, telegram
from wabot.dashboard import trainee_pages
from wabot.db import SessionLocal
from wabot.models import PlanTemplate, Trainee

from .test_dashboard import people, settings, sign_in
from .test_telegram import contact, post, telegram_sent

BOT = "https://t.me/YanekTestBot"


@pytest.fixture(autouse=True)
def bot_link(monkeypatch):
    monkeypatch.setattr(trainee_pages, "bot_link", lambda settings: BOT)


def add(client, **fields):
    form = {"name": "Basia", "phone": "792 541 888", "consent_date": "2026-11-10", **fields}
    return client.post("/dashboard/trainees/new", data=form, follow_redirects=False)


def trainee_named(name: str) -> Trainee | None:
    with SessionLocal() as session:
        return session.scalar(select(Trainee).where(Trainee.name == name))


def library_plan(trainer) -> PlanTemplate:
    with SessionLocal() as session:
        template = PlanTemplate(
            trainer_id=trainer.id,
            name="FBW dla początkujących",
            plan=plans.to_db(plans.load(Path("plans/beginner_fbw.yaml"))),
        )
        session.add(template)
        session.commit()
        return template


def test_the_form(client, settings, people):
    library_plan(people["Trener Marek"])
    sign_in(client, people["Trener Marek"])
    page = client.get("/dashboard/trainees/new").text
    assert 'name="phone"' in page and 'name="consent_date"' in page
    assert "FBW dla początkujących" in page and "Bez planu" in page
    assert "Dodaj podopiecznego" in client.get("/dashboard").text  # sidebar and button


def test_adding_a_trainee(client, settings, people):
    marek = people["Trener Marek"]
    sign_in(client, marek)
    response = add(client)
    assert response.status_code == 303
    basia = trainee_named("Basia")
    assert response.headers["location"] == f"/dashboard?added={basia.id}"
    # A Polish number without the country code gets 48.
    assert (basia.phone, basia.trainer_id, basia.consent_date, basia.timezone) == (
        "48792541888",
        marek.id,
        dt.date(2026, 11, 10),
        "Europe/Warsaw",
    )
    assert basia.telegram_chat_id is None and basia.plan_version_id is None

    page = client.get(response.headers["location"]).text
    assert "Dodano: Basia." in page and BOT in page and "+48792541888" in page
    assert "jeszcze nie połączony" in page


def test_adding_a_trainee_with_a_library_plan(client, settings, people):
    marek = people["Trener Marek"]
    template = library_plan(marek)
    sign_in(client, marek)
    add(client, phone="+48 600 100 200", template_id=str(template.id))
    basia = trainee_named("Basia")
    assert basia.phone == "48600100200"
    with SessionLocal() as session:
        [version] = plan_history.history(session, session.get(Trainee, basia.id))
    assert (version.template_id, version.source) == (template.id, "template")


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"name": "  "}, "Podaj imię podopiecznego."),
        ({"phone": "12"}, "Podaj numer telefonu"),
        ({"phone": "48000000001"}, "Ten numer ma już Kasia."),  # Marek's own trainee
        ({"phone": "48000000003"}, "zarejestrowany u innego trenera"),  # Ewa's Ola: not who
        ({"consent_date": ""}, "Podaj datę podpisania zgody."),
        ({"consent_date": "2999-01-01"}, "nie może być w przyszłości"),
    ],
)
def test_a_wrong_form_is_shown_again_with_the_error(client, settings, people, fields, error):
    sign_in(client, people["Trener Marek"])
    response = add(client, **{"name": "Basia", **fields})
    assert response.status_code == 422
    assert error in response.text
    assert "Ola" not in response.text  # another trainer's trainee isn't named
    with SessionLocal() as session:
        assert session.scalar(select(Trainee).where(Trainee.name == "Basia")) is None
    # What was typed stays in the form.
    assert f'value="{fields.get("phone", "792 541 888")}"' in response.text


def test_another_trainers_library_plan_cant_be_given(client, settings, people):
    ewas = library_plan(people["Trenerka Ewa"])
    sign_in(client, people["Trener Marek"])
    response = add(client, template_id=str(ewas.id))
    assert response.status_code == 422 and "Wybierz plan ze swojej biblioteki." in response.text
    assert trainee_named("Basia") is None


def test_adding_needs_a_signed_in_trainer(client, settings, people):
    response = add(client)
    assert response.status_code in (303, 401)
    assert trainee_named("Basia") is None


def test_the_added_notice_is_only_for_the_trainers_own_trainee(client, settings, people):
    sign_in(client, people["Trener Marek"])
    ola = people["Ola"]  # Ewa's
    assert "Dodano:" not in client.get(f"/dashboard?added={ola.id}").text
    assert "Dodano:" not in client.get("/dashboard?added=abc").text


def test_an_added_trainee_connects_on_telegram(client, settings, people, telegram_sent):
    sign_in(client, people["Trener Marek"])
    add(client)
    post(client, contact(1, "+48792541888"))
    basia = trainee_named("Basia")
    assert basia.telegram_chat_id is not None
    assert "połączony" in client.get("/dashboard").text
    assert telegram_sent[0].keyboard == telegram.REMOVE_KEYBOARD  # the welcome
