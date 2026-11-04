"""The dashboard: signing in, and that a trainer sees only their own trainees."""

import datetime as dt
import json

import pytest

from wabot.config import Settings, get_settings
from wabot.dashboard import auth, queries, routes
from wabot.db import SessionLocal
from wabot.main import app
from wabot.models import Message, Trainee, Trainer, WeightEntry

SECTIONS = ["weight", "trainings", "plan"]


@pytest.fixture
def settings():
    settings = Settings(dashboard_dev_login=True)
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


@pytest.fixture
def people():
    """Two trainers: Marek with Kasia and Tomek, Ewa with Ola."""
    with SessionLocal() as session:
        marek = Trainer(name="Trener Marek", email="marek.trener@example.com")
        ewa = Trainer(name="Trenerka Ewa", email="ewa.trenerka@example.com")
        kasia, tomek, ola = (
            Trainee(trainer=trainer, phone=phone, name=name, consent_date=dt.date(2026, 10, 1))
            for trainer, phone, name in [
                (marek, "48000000001", "Kasia"),
                (marek, "48000000002", "Tomek"),
                (ewa, "48000000003", "Ola"),
            ]
        )
        session.add_all([marek, ewa, kasia, tomek, ola])
        session.commit()
        return {p.name: p for p in (marek, ewa, kasia, tomek, ola)}


def sign_in(client, trainer):
    response = client.post(
        "/dashboard/login/dev", data={"trainer_id": trainer.id}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_pages_need_a_signed_in_trainer(client, settings, people):
    for path in ["/dashboard"] + [
        f"/dashboard/trainees/{people['Kasia'].id}/{s}" for s in SECTIONS
    ]:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/dashboard/login"


def test_dev_login_lists_the_trainers(client, settings, people):
    page = client.get("/dashboard/login").text
    assert "Trener Marek" in page and "Trenerka Ewa" in page


def test_without_dev_login_there_is_no_trainer_list_and_no_dev_sign_in(client, people):
    app.dependency_overrides[get_settings] = lambda: Settings(dashboard_session_secret="s")
    try:
        assert "Trener Marek" not in client.get("/dashboard/login").text
        response = client.post(
            "/dashboard/login/dev", data={"trainer_id": people["Trener Marek"].id}
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_a_trainer_sees_only_their_own_trainees(client, settings, people):
    sign_in(client, people["Trener Marek"])

    overview = client.get("/dashboard")
    assert overview.status_code == 200
    assert "Kasia" in overview.text and "Tomek" in overview.text
    assert "Ola" not in overview.text

    for section in SECTIONS:
        own = client.get(f"/dashboard/trainees/{people['Kasia'].id}/{section}")
        assert own.status_code == 200, section
        assert "Ola" not in own.text, section  # not in the dropdown either
        other = client.get(f"/dashboard/trainees/{people['Ola'].id}/{section}")
        assert other.status_code == 404, section
        missing = client.get(f"/dashboard/trainees/9999/{section}")
        assert missing.status_code == 404, section


def test_the_dropdown_opens_the_same_page_for_another_trainee(client, settings, people):
    sign_in(client, people["Trener Marek"])
    kasia, tomek = people["Kasia"], people["Tomek"]
    page = client.get(f"/dashboard/trainees/{kasia.id}/weight").text
    assert f'value="/dashboard/trainees/{tomek.id}/weight"' in page
    # From the overview it opens Treningi.
    page = client.get("/dashboard").text
    assert f'value="/dashboard/trainees/{tomek.id}/trainings"' in page


def test_a_tampered_cookie_is_not_a_session(client, settings, people):
    sign_in(client, people["Trener Marek"])
    cookie = client.cookies[auth.COOKIE]
    client.cookies.clear()
    # Changing the trainer ID breaks the signature.
    client.cookies[auth.COOKIE] = cookie.replace(cookie[0], "9" if cookie[0] != "9" else "8", 1)
    assert client.get("/dashboard", follow_redirects=False).status_code == 303


def test_a_cookie_signed_with_another_secret_is_not_a_session(client, people):
    app.dependency_overrides[get_settings] = lambda: Settings(dashboard_dev_login=True)
    sign_in(client, people["Trener Marek"])
    # The dev key no longer counts once there's a real secret and no dev login.
    app.dependency_overrides[get_settings] = lambda: Settings(dashboard_session_secret="real")
    try:
        assert client.get("/dashboard", follow_redirects=False).status_code == 303
    finally:
        app.dependency_overrides.clear()


def test_logout(client, settings, people):
    sign_in(client, people["Trener Marek"])
    response = client.post("/dashboard/logout", follow_redirects=False)
    assert response.headers["location"] == "/dashboard/login"
    assert client.get("/dashboard", follow_redirects=False).status_code == 303


def test_static_files_are_served(client):
    assert client.get("/dashboard/static/app.css").status_code == 200
    assert client.get("/dashboard/static/app.js").status_code == 200


@pytest.fixture
def google(monkeypatch, people):
    """Google sign-in configured; Google's token check replaced: the token is the claims."""
    settings = Settings(google_client_id="client-123", dashboard_session_secret="secret")
    app.dependency_overrides[get_settings] = lambda: settings

    def verify(token, request, client_id):
        assert client_id == "client-123"
        if token == "forged":
            raise ValueError("Could not verify token signature.")
        email, verified = token.split("|")
        return {"email": email, "email_verified": verified == "verified"}

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", verify)
    yield
    app.dependency_overrides.clear()


def google_post(client, token, csrf="abc", cookie="abc"):
    client.cookies.set("g_csrf_token", cookie)
    return client.post(
        "/dashboard/login/google",
        data={"credential": token, "g_csrf_token": csrf},
        follow_redirects=False,
    )


def test_the_login_page_shows_googles_button(client, google):
    page = client.get("/dashboard/login").text
    assert 'data-client_id="client-123"' in page
    assert 'data-login_uri="http://testserver/dashboard/login/google"' in page
    assert "Trener Marek" not in page  # no dev login


def test_google_sign_in_finds_the_trainer_by_email(client, google, people):
    response = google_post(client, "Marek.Trener@Example.com|verified")
    assert response.status_code == 303
    page = client.get("/dashboard").text
    assert "Kasia" in page and "Ola" not in page


@pytest.mark.parametrize(
    "token, status",
    [
        ("someone@gmail.com|verified", 403),  # not a trainer
        ("marek.trener@example.com|unverified", 401),  # Google didn't verify the email
        ("forged", 401),  # not signed by Google
    ],
)
def test_google_sign_in_refused(client, google, token, status):
    response = google_post(client, token)
    assert response.status_code == status
    assert auth.COOKIE not in client.cookies
    assert client.get("/dashboard", follow_redirects=False).status_code == 303


def test_google_sign_in_needs_the_csrf_cookie(client, google):
    response = google_post(client, "marek.trener@example.com|verified", cookie="other")
    assert response.status_code == 400


def test_google_sign_in_is_off_without_a_client_id(client, settings):
    response = google_post(client, "marek.trener@example.com|verified")
    assert response.status_code == 404


def add_weight(trainee, day, kg, text):
    """A weight report as the bot saves it: the message, then the entry pointing at it."""
    with SessionLocal() as session:
        message = Message(
            direction="in",
            phone=trainee.phone,
            type="text",
            body=text,
            trainee_id=trainee.id,
            trainer_id=trainee.trainer_id,
        )
        session.add(message)
        session.flush()
        session.add(
            WeightEntry(
                trainer_id=trainee.trainer_id,
                trainee_id=trainee.id,
                date=day,
                kg=kg,
                message_id=message.id,
            )
        )
        session.commit()


def test_the_weight_page_shows_the_current_weight_of_each_day(client, settings, people):
    kasia, ola = people["Kasia"], people["Ola"]
    add_weight(kasia, dt.date(2026, 10, 2), 82.4, "yanek waga 82,4")
    add_weight(kasia, dt.date(2026, 10, 9), 18.9, "yanek 18,9")
    add_weight(kasia, dt.date(2026, 10, 9), 81.9, "yanek sorki, 81,9")  # the correction counts
    add_weight(ola, dt.date(2026, 10, 9), 65.0, "yanek 65")

    with SessionLocal() as session:
        weigh_ins = queries.weigh_ins(session, session.get(Trainee, kasia.id))
    assert [(w.date.day, w.kg, w.message, w.reports) for w in weigh_ins] == [
        (2, 82.4, "yanek waga 82,4", 1),
        (9, 81.9, "yanek sorki, 81,9", 2),
    ]

    sign_in(client, people["Trener Marek"])
    page = client.get(f"/dashboard/trainees/{kasia.id}/weight").text
    assert "81,9 kg" in page and "−0,5 kg" in page  # last weight, change since the first
    assert "pt 9 paź 2026" in page
    assert "„yanek sorki, 81,9”" in page and "poprawiona" in page
    assert "18,9" not in page and "65,0" not in page  # the replaced report, Ola's weight
    data = page.split('<script type="application/json" id="weight-data">')[1].split("</script>")[0]
    assert json.loads(data) == [
        {"date": "2026-10-02", "kg": 82.4, "message": "yanek waga 82,4"},
        {"date": "2026-10-09", "kg": 81.9, "message": "yanek sorki, 81,9"},
    ]


def test_the_chart_data_cant_break_out_of_its_script_tag(client, settings, people):
    add_weight(people["Kasia"], dt.date(2026, 10, 2), 82.4, "</script><script>alert(1)</script>")
    sign_in(client, people["Trener Marek"])
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/weight").text
    assert "<script>alert(1)" not in page


def test_the_weight_page_without_weigh_ins(client, settings, people):
    sign_in(client, people["Trener Marek"])
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/weight").text
    assert "Brak zgłoszonej wagi" in page and "weight-data" not in page


def test_trainee_pages_say_to_choose_a_trainee_first(client, settings, people):
    sign_in(client, people["Trener Marek"])
    page = client.get("/dashboard").text
    assert page.count('data-hint="Wybierz podopiecznego"') == len(SECTIONS)
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/weight").text
    assert "data-hint" not in page


def test_polish_formats():
    assert routes.pl_date(dt.date(2026, 10, 5), weekday=True) == "pon 5 paź 2026"
    assert routes.pl_kg(82.4) == "82,4 kg"
    assert routes.pl_kg(81.2 - 82.4, signed=True) == "−1,2 kg"
    assert routes.pl_kg(0.4, signed=True) == "+0,4 kg"
