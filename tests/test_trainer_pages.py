"""Trenerzy: admins (ADMIN_EMAILS) add trainers by name and Google email; the new trainer can
then sign in with Google. Nobody else sees the page."""

# The fixtures shared with test_dashboard.py are imported, then used as test arguments.
# ruff: noqa: F401, F811

import pytest
from sqlalchemy import select

from wabot.config import Settings, get_settings
from wabot.dashboard import auth
from wabot.db import SessionLocal
from wabot.main import app
from wabot.models import Trainer

from .test_dashboard import google_post, people, sign_in


@pytest.fixture
def settings():
    """Marek is an admin (any case in ADMIN_EMAILS); Ewa isn't."""
    settings = Settings(dashboard_dev_login=True, admin_emails=" Marek.Trener@example.com ,x@y.pl")
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


def add(client, **fields):
    form = {"name": "Anna Nowak", "email": "Anna.Nowak@Gmail.com", **fields}
    return client.post("/dashboard/trainers", data=form, follow_redirects=False)


def trainer_named(name: str) -> Trainer | None:
    with SessionLocal() as session:
        return session.scalar(select(Trainer).where(Trainer.name == name))


def test_an_admin_adds_a_trainer(client, settings, people):
    sign_in(client, people["Trener Marek"])
    page = client.get("/dashboard/trainers").text
    assert "Trenerka Ewa" in page and "ewa.trenerka@example.com" in page
    response = add(client)
    anna = trainer_named("Anna Nowak")
    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/trainers?added={anna.id}"
    assert anna.email == "anna.nowak@gmail.com"  # stored lowercase, as Google is compared
    page = client.get(response.headers["location"]).text
    assert "Dodano: Anna Nowak." in page and "Test users" in page


def test_the_sidebar_shows_trenerzy_to_admins_only(client, settings, people):
    sign_in(client, people["Trener Marek"])
    assert 'href="/dashboard/trainers"' in client.get("/dashboard").text
    client.cookies.clear()
    sign_in(client, people["Trenerka Ewa"])
    assert 'href="/dashboard/trainers"' not in client.get("/dashboard").text


def test_other_trainers_cant_see_or_use_the_page(client, settings, people):
    sign_in(client, people["Trenerka Ewa"])
    assert client.get("/dashboard/trainers").status_code == 404
    assert add(client).status_code == 404
    assert trainer_named("Anna Nowak") is None


def test_without_signing_in_there_is_no_page(client, settings, people):
    assert client.get("/dashboard/trainers", follow_redirects=False).status_code in (303, 401)
    assert add(client).status_code in (303, 401)
    assert trainer_named("Anna Nowak") is None


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"name": " "}, "Podaj imię i nazwisko trenera."),
        ({"name": "Trenerka Ewa"}, "Trener o tej nazwie już jest."),
        ({"email": "not-an-email"}, "Podaj adres Gmail"),
        ({"email": "EWA.trenerka@example.com"}, "Tym adresem loguje się już Trenerka Ewa."),
    ],
)
def test_a_wrong_form_is_shown_again(client, settings, people, fields, error):
    sign_in(client, people["Trener Marek"])
    response = add(client, **fields)
    assert response.status_code == 422 and error in response.text
    assert trainer_named("Anna Nowak") is None


def test_the_added_trainer_signs_in_with_google(client, settings, people, monkeypatch):
    sign_in(client, people["Trener Marek"])
    add(client)
    client.cookies.clear()
    # Now with Google sign-in (no dev login); Google's check of the token replaced.
    google_settings = Settings(
        google_client_id="client-123",
        dashboard_session_secret="secret",
        admin_emails=settings.admin_emails,
    )
    app.dependency_overrides[get_settings] = lambda: google_settings
    monkeypatch.setattr(
        auth.id_token,
        "verify_oauth2_token",
        lambda token, request, client_id: {"email": token, "email_verified": True},
    )
    response = google_post(client, "Anna.Nowak@gmail.com")
    assert response.status_code == 303 and response.headers["location"] == "/dashboard"
    page = client.get("/dashboard").text
    assert "Anna Nowak" in page and "Nie masz jeszcze podopiecznych" in page
    assert 'href="/dashboard/trainers"' not in page  # not an admin
