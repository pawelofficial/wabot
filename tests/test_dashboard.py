"""The dashboard: signing in, that a trainer sees only their own trainees, and the pages."""

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from wabot import assistant, entries, exercises, llm_output, plan_history, plans
from wabot.config import Settings, get_settings
from wabot.dashboard import auth, plan_editor, queries, routes
from wabot.db import SessionLocal
from wabot.main import app
from wabot.models import (
    Message,
    PlanTemplate,
    Trainee,
    TraineePlan,
    Trainer,
    TrainingExercise,
    WeightEntry,
)

PLAN_DIR = Path(__file__).parents[1] / "plans"
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
    assert routes.pl_month(dt.date(2026, 10, 1)) == "październik 2026"
    assert routes.pl_month(dt.date(2026, 11, 1), in_=True) == "w listopadzie 2026"
    assert (routes.pl_number(62.5), routes.pl_number(60.0)) == ("62,5", "60")

    def done(sets, reps, kg):
        return routes.pl_dose(TrainingExercise(sets=sets, reps=reps, weight_kg=kg))

    assert done(4, "8", 62.5) == "4 × 8, 62,5 kg"
    assert (done(3, None, None), done(None, "28 min", None), done(None, None, 20)) == (
        "3 serie",
        "28 min",
        "20 kg",
    )
    assert routes.pl_kg(82.4) == "82,4 kg"
    assert routes.pl_kg(81.2 - 82.4, signed=True) == "−1,2 kg"
    assert routes.pl_kg(0.4, signed=True) == "+0,4 kg"


# Plan treningowy


def give_plan(trainee, plan_file="upper_lower.yaml"):
    """As scripts/add_trainee.py does: the plan's first version."""
    with SessionLocal() as session:
        plan_history.assign(
            session, session.get(Trainee, trainee.id), plans.load(PLAN_DIR / plan_file), "script"
        )
        session.commit()


def stored_plan(trainee):
    with SessionLocal() as session:
        version = session.get(Trainee, trainee.id).plan_version
        return version.plan if version else None


def edited(**changes):
    """The upper/lower plan as the editor sends it, with `changes` applied to it by key."""
    data = plan_editor.form_data(plans.load(PLAN_DIR / "upper_lower.yaml"))
    for key, value in changes.items():
        target = data
        *path, last = key.split("__")
        for part in path:
            target = target[int(part) if part.isdigit() else part]
        target[int(last) if last.isdigit() else last] = value
    return json.dumps(data)


def save(client, trainee, plan_json):
    return client.post(
        f"/dashboard/trainees/{trainee.id}/plan/edit",
        data={"plan": plan_json},
        follow_redirects=False,
    )


def test_the_plan_page_shows_days_exercises_and_replacements(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan").text
    assert "Poniedziałek – góra 1" in page and "Piątek – dół 2" in page
    assert "Wyciskanie sztangi leżąc" in page  # catalog names, not IDs
    assert "Wyciskanie hantli na ławce skośnej" in page  # a replacement
    assert "Białko min. 160 g dziennie." in page  # the notes
    assert "/plan/edit" in page


def test_a_trainee_without_a_plan_gets_a_create_button_and_one_empty_day(client, settings, people):
    sign_in(client, people["Trener Marek"])
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan").text
    assert "nie ma jeszcze planu" in page and "Utwórz plan" in page
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan/edit").text
    assert "Nowy plan – Kasia" in page
    # The one day and the template for "+ Dodaj dzień", neither with an ID yet.
    assert page.count('data-day data-day-id="">') == 2


def test_the_editor_starts_with_the_trainees_plan(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan/edit").text
    assert 'value="Wtorek – dół 1"' in page
    assert '<option value="back_squat" selected>' in page
    assert 'data-replacement="leg_press"' in page
    assert 'value="2 x 28 kg"' in page


def test_saving_the_plan(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    response = save(
        client,
        people["Kasia"],
        edited(days__0__name="  Poniedziałek – góra  ", days__0__exercises__0__sets="5", notes=""),
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/plan?done=saved")
    with SessionLocal() as session:
        plan = plans.of(session.get(Trainee, people["Kasia"].id))
    assert plan.days[0].name == "Poniedziałek – góra"
    assert plan.days[0].exercises[0].sets == 5
    assert plan.notes is None
    # Empty fields aren't stored as empty text.
    assert "note" not in stored_plan(people["Kasia"])["days"][0]["exercises"][0]
    page = client.get(response.headers["location"]).text
    assert "Zapisano plan" in page


def test_a_new_plan_can_be_saved(client, settings, people):
    sign_in(client, people["Trener Marek"])
    plan_json = json.dumps(
        {"notes": "", "days": [{"name": "A", "exercises": [{"id": "plank", "reps": "45 s"}]}]}
    )
    assert save(client, people["Kasia"], plan_json).status_code == 303
    stored = stored_plan(people["Kasia"])
    day_id = stored["days"][0].pop("id")  # the new day got an ID when it was stored
    assert len(day_id) == 8
    assert stored == {"days": [{"name": "A", "exercises": [{"id": "plank", "reps": "45 s"}]}]}


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"days__1__name": " "}, "Dzień 2: podaj nazwę dnia"),
        (
            {"days__1__name": "poniedziałek – GÓRA 1"},
            "Dwa dni mają tę samą nazwę („Poniedziałek – góra 1”)",
        ),
        ({"days": []}, "Plan musi mieć co najmniej jeden dzień"),
        (
            {"days__1__exercises": []},
            "Dzień 2 „Wtorek – dół 1”: dodaj co najmniej jedno ćwiczenie",
        ),
        (
            {"days__1__exercises__1__id": "back_squat"},
            "Dzień 2 „Wtorek – dół 1”: Przysiad ze sztangą jest w tym dniu dwa razy",
        ),
        (
            {"days__0__exercises__0__id": ""},
            "Dzień 1 „Poniedziałek – góra 1”, ćwiczenie 1: wybierz ćwiczenie",
        ),
        ({"days__0__exercises__0__id": "squat_jump"}, "ćwiczenie 1: nieznane ćwiczenie"),
        (
            {"days__0__exercises__0__replacements": ["bench_press"]},
            "ćwiczenie 1 (Wyciskanie sztangi leżąc): ćwiczenie nie może być swoim",
        ),
        (
            {"days__0__exercises__0__replacements": ["dumbbell_row", "dumbbell_row"]},
            "ten sam zamiennik jest dodany dwa razy",
        ),
        ({"days__0__exercises__0__replacements": ["nope"]}, "nieznany zamiennik"),
        ({"days__0__exercises__0__sets": "0"}, "liczba serii musi być liczbą całkowitą"),
        ({"days__0__exercises__0__sets": "dużo"}, "liczba serii musi być liczbą całkowitą"),
        ({"days__1__label": "pn"}, "Dwa dni mają to samo oznaczenie („Pn”)"),
        (
            {"days__1__label": "Wtor"},
            "Dzień 2 „Wtorek – dół 1”: oznaczenie dnia może mieć najwyżej 3 znaki",
        ),
    ],
)
def test_a_bad_plan_isnt_saved_and_the_editor_says_why(client, settings, people, changes, error):
    give_plan(people["Kasia"])
    before = stored_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    response = save(client, people["Kasia"], edited(**changes))
    assert response.status_code == 422
    assert "Plan nie został zapisany" in response.text
    assert error in response.text
    assert stored_plan(people["Kasia"]) == before


def test_the_editor_gives_every_day_a_label(client, settings, people):
    # A plan whose days have no labels starts with A, B, C by position.
    plan = plans.load(PLAN_DIR / "upper_lower.yaml").model_copy(deep=True)
    for day in plan.days:
        day.label = None
    assert [d["label"] for d in plan_editor.form_data(plan)["days"]] == ["A", "B", "C", "D"]
    assert plan_editor.form_data(None)["days"][0]["label"] == "A"

    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan/edit").text
    assert 'data-field="label" value="Pn"' in page
    # The trainer's label moves with its day; a label left empty is the day's position.
    data = json.loads(edited(days__1__label="D1", days__3__label=""))
    data["days"].reverse()
    assert save(client, people["Kasia"], json.dumps(data)).status_code == 303
    stored = [(d["name"], d.get("label")) for d in stored_plan(people["Kasia"])["days"]]
    assert stored == [
        ("Piątek – dół 2", None),
        ("Czwartek – góra 2", "Cz"),
        ("Wtorek – dół 1", "D1"),
        ("Poniedziałek – góra 1", "Pn"),
    ]
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan").text
    assert '<span class="day-label" title="Oznaczenie w kalendarzu treningów">A</span>' in page


def test_a_bad_plan_is_shown_again_as_typed(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    response = save(
        client, people["Kasia"], edited(days__1__name="", days__0__exercises__0__weight="82,5 kg")
    )
    assert response.status_code == 422
    assert 'value="82,5 kg"' in response.text
    assert "data-unsaved" in response.text  # leaving asks first: nothing is saved yet


@pytest.mark.parametrize("plan_json", ["not json", "[]", '{"days": "x"}', '{"days": [1, {}]}'])
def test_a_broken_submission_is_an_error_not_a_crash(client, settings, people, plan_json):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    response = save(client, people["Kasia"], plan_json)
    assert response.status_code == 422
    assert "Plan nie został zapisany" in response.text


def test_a_trainer_cant_edit_another_trainers_plan(client, settings, people):
    give_plan(people["Ola"], "beginner_fbw.yaml")
    before = stored_plan(people["Ola"])
    sign_in(client, people["Trener Marek"])
    assert client.get(f"/dashboard/trainees/{people['Ola'].id}/plan/edit").status_code == 404
    assert save(client, people["Ola"], edited()).status_code == 404
    assert stored_plan(people["Ola"]) == before


def test_saving_a_plan_needs_a_signed_in_trainer(client, settings, people):
    response = save(client, people["Kasia"], edited())
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/login"
    assert stored_plan(people["Kasia"]) is None


def test_the_bot_uses_the_edited_plan(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    save(client, people["Kasia"], edited(days__0__name="Dzień nowy"))
    with SessionLocal() as session:
        plan = plans.of(session.get(Trainee, people["Kasia"].id))
    assert "Dzień nowy" in assistant.build_instructions(plan)


# Plan history and the plan library


def versions(trainee):
    with SessionLocal() as session:
        rows = session.scalars(
            select(TraineePlan).where(TraineePlan.trainee_id == trainee.id).order_by(TraineePlan.id)
        ).all()
        return [(v.source, v.name, v.template_id, v.plan["days"][0]["name"]) for v in rows]


def add_template(trainer, name, plan_file="beginner_fbw.yaml"):
    with SessionLocal() as session:
        template = PlanTemplate(
            trainer_id=trainer.id,
            name=name,
            plan=plans.to_db(plans.load(PLAN_DIR / plan_file)),
        )
        session.add(template)
        session.commit()
        return template


def library_json(day_name="Trening 1"):
    return json.dumps(
        {"notes": "", "days": [{"name": day_name, "exercises": [{"id": "plank", "sets": "3"}]}]}
    )


def test_every_change_of_a_trainees_plan_is_a_version(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    save(client, people["Kasia"], edited(days__0__name="Poniedziałek – góra"))
    # Saving without a change adds nothing.
    response = save(client, people["Kasia"], edited(days__0__name="Poniedziałek – góra"))
    assert response.headers["location"].endswith("?done=unchanged")
    assert versions(people["Kasia"]) == [
        ("script", None, None, "Poniedziałek – góra 1"),
        ("edited", None, None, "Poniedziałek – góra"),
    ]
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan").text
    assert "Historia planów" in page and "Wcześniejsze plany (1)" in page


def test_an_earlier_plan_can_be_previewed_and_restored(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    save(client, people["Kasia"], edited(days__0__name="Nowa nazwa"))
    with SessionLocal() as session:
        first = plan_history.history(session, session.get(Trainee, people["Kasia"].id))[-1]
    url = f"/dashboard/trainees/{people['Kasia'].id}/plan/versions/{first.id}"
    page = client.get(url).text
    assert "Wcześniejszy plan" in page and "Poniedziałek – góra 1" in page
    response = client.post(url + "/restore", follow_redirects=False)
    assert response.headers["location"].endswith("?done=restored")
    assert [v[0] for v in versions(people["Kasia"])] == ["script", "edited", "restored"]
    with SessionLocal() as session:
        trainee = session.get(Trainee, people["Kasia"].id)
        assert plans.of(trainee).days[0].name == "Poniedziałek – góra 1"  # what the bot reads


def test_a_version_of_another_trainee_is_404(client, settings, people):
    give_plan(people["Kasia"])
    give_plan(people["Ola"], "beginner_fbw.yaml")
    sign_in(client, people["Trener Marek"])
    with SessionLocal() as session:
        kasias = plan_history.history(session, session.get(Trainee, people["Kasia"].id))[0]
        olas = plan_history.history(session, session.get(Trainee, people["Ola"].id))[0]
    tomek = people["Tomek"].id
    assert client.get(f"/dashboard/trainees/{tomek}/plan/versions/{kasias.id}").status_code == 404
    response = client.post(f"/dashboard/trainees/{tomek}/plan/versions/{kasias.id}/restore")
    assert response.status_code == 404
    kasia = people["Kasia"].id
    assert client.get(f"/dashboard/trainees/{kasia}/plan/versions/{olas.id}").status_code == 404
    assert stored_plan(people["Tomek"]) is None


def test_a_library_plan_is_created_and_listed(client, settings, people):
    sign_in(client, people["Trener Marek"])
    assert "Biblioteka planów" in client.get("/dashboard").text  # in the sidebar
    response = client.post(
        "/dashboard/plans/new",
        data={"name": "  FBW   dla początkujących ", "plan": library_json()},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "FBW dla początkujących" in page and "Plank" in page
    assert "FBW dla początkujących" in client.get("/dashboard/plans").text


@pytest.mark.parametrize(
    ("name", "error"),
    [("", "Podaj nazwę planu."), ("fbw", "W bibliotece jest już plan „FBW”")],
)
def test_a_library_plan_needs_a_name_of_its_own(client, settings, people, name, error):
    add_template(people["Trener Marek"], "FBW")
    add_template(people["Trenerka Ewa"], "Ewy plan")
    sign_in(client, people["Trener Marek"])
    response = client.post("/dashboard/plans/new", data={"name": name, "plan": library_json()})
    assert response.status_code == 422
    assert error in response.text
    # Another trainer's names don't count.
    response = client.post(
        "/dashboard/plans/new",
        data={"name": "Ewy plan", "plan": library_json()},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_assigning_a_library_plan_gives_the_trainee_a_copy(client, settings, people):
    template = add_template(people["Trener Marek"], "FBW")
    sign_in(client, people["Trener Marek"])
    kasia = people["Kasia"]
    response = client.post(
        f"/dashboard/trainees/{kasia.id}/plan/assign",
        data={"template_id": template.id},
        follow_redirects=False,
    )
    assert response.headers["location"].endswith("?done=assigned")
    assert versions(kasia) == [("template", "FBW", template.id, "Trening 1")]
    page = client.get(f"/dashboard/trainees/{kasia.id}/plan").text
    assert "FBW" in page and "z biblioteki" in page
    assert "Kasia" in client.get(f"/dashboard/plans/{template.id}").text  # who has it

    # Changing the library plan doesn't change Kasia's.
    client.post(
        f"/dashboard/plans/{template.id}/edit",
        data={"name": "FBW v2", "plan": library_json("Zmieniony")},
    )
    assert stored_plan(kasia)["days"][0]["name"] == "Trening 1"
    # Kasia's edit keeps the link to the library plan it's based on.
    save(client, kasia, json.dumps(plan_editor.form_data(plans.load(PLAN_DIR / "example.yaml"))))
    assert versions(kasia)[-1][:3] == ("edited", "FBW", template.id)
    assert "(zmieniony)" in client.get("/dashboard/plans").text

    # Deleting it neither: the history keeps its name.
    response = client.post(f"/dashboard/plans/{template.id}/delete", follow_redirects=False)
    assert response.headers["location"] == "/dashboard/plans?done=deleted"
    assert stored_plan(kasia) is not None
    assert versions(kasia)[0] == ("template", "FBW", None, "Trening 1")


def test_a_trainees_plan_can_be_copied_to_the_library(client, settings, people):
    give_plan(people["Tomek"])
    add_template(people["Trener Marek"], "Zajęta nazwa")
    sign_in(client, people["Trener Marek"])
    url = f"/dashboard/trainees/{people['Tomek'].id}/plan/to-library"
    response = client.post(url, data={"name": "zajęta nazwa"})
    assert response.status_code == 422 and "W bibliotece jest już plan" in response.text
    response = client.post(url, data={"name": "Góra/dół"}, follow_redirects=False)
    assert response.headers["location"].endswith("?done=to_library")
    with SessionLocal() as session:
        [template] = [
            t
            for t in plan_history.library(session, people["Trener Marek"].id)
            if t.name == "Góra/dół"
        ]
        assert template.plan == stored_plan(people["Tomek"])


def test_a_trainer_cant_reach_another_trainers_library(client, settings, people):
    ewas = add_template(people["Trenerka Ewa"], "Ewy plan")
    sign_in(client, people["Trener Marek"])
    assert "Ewy plan" not in client.get("/dashboard/plans").text
    assert client.get(f"/dashboard/plans/{ewas.id}").status_code == 404
    assert client.get(f"/dashboard/plans/{ewas.id}/edit").status_code == 404
    response = client.post(
        f"/dashboard/plans/{ewas.id}/edit", data={"name": "x", "plan": library_json()}
    )
    assert response.status_code == 404
    assert client.post(f"/dashboard/plans/{ewas.id}/delete").status_code == 404
    response = client.post(
        f"/dashboard/trainees/{people['Kasia'].id}/plan/assign", data={"template_id": ewas.id}
    )
    assert response.status_code == 404
    assert stored_plan(people["Kasia"]) is None
    with SessionLocal() as session:
        assert session.get(PlanTemplate, ewas.id).name == "Ewy plan"


def test_the_library_pages_need_a_signed_in_trainer(client, settings, people):
    for path in ["/dashboard/plans", "/dashboard/plans/new"]:
        response = client.get(path, follow_redirects=False)
        assert response.headers["location"] == "/dashboard/login"


def test_a_change_that_changes_nothing_adds_no_version(client, settings, people):
    template = add_template(people["Trener Marek"], "FBW")
    sign_in(client, people["Trener Marek"])
    kasia = people["Kasia"]
    url = f"/dashboard/trainees/{kasia.id}/plan"
    client.post(url + "/assign", data={"template_id": template.id})
    # The same library plan again: nothing to change.
    response = client.post(
        url + "/assign", data={"template_id": template.id}, follow_redirects=False
    )
    assert response.headers["location"].endswith("?done=already_assigned")
    save(client, kasia, edited())
    # Restoring the version equal to the current plan.
    with SessionLocal() as session:
        current = plan_history.history(session, session.get(Trainee, kasia.id))[0]
    response = client.post(f"{url}/versions/{current.id}/restore", follow_redirects=False)
    assert response.headers["location"].endswith("?done=unchanged")
    assert [v[0] for v in versions(kasia)] == ["template", "edited"]


def test_a_removed_plan_comes_back_when_the_same_plan_is_saved(client, settings, people):
    give_plan(people["Kasia"])
    with SessionLocal() as session:  # as scripts/add_trainee.py --no-plan does
        plan_history.remove(session, session.get(Trainee, people["Kasia"].id))
        session.commit()
    sign_in(client, people["Trener Marek"])
    response = save(client, people["Kasia"], edited())
    assert response.headers["location"].endswith("?done=saved")
    assert stored_plan(people["Kasia"]) is not None


def test_a_trainee_moved_to_another_trainer_isnt_in_the_old_trainers_library(
    client, settings, people
):
    template = add_template(people["Trener Marek"], "FBW")
    sign_in(client, people["Trener Marek"])
    client.post(
        f"/dashboard/trainees/{people['Kasia'].id}/plan/assign", data={"template_id": template.id}
    )
    assert "Kasia" in client.get("/dashboard/plans").text
    # Trainees never move (add_trainee.py refuses); if a row changed by hand, nothing leaks.
    with SessionLocal() as session:
        session.get(Trainee, people["Kasia"].id).trainer_id = people["Trenerka Ewa"].id
        session.commit()
    assert "Kasia" not in client.get("/dashboard/plans").text
    assert "Kasia" not in client.get(f"/dashboard/plans/{template.id}").text


def test_the_dev_login_refuses_to_run_on_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "wabot")
    with pytest.raises(ValueError, match="DASHBOARD_DEV_LOGIN"):
        Settings(dashboard_dev_login=True)
    assert not Settings().dashboard_dev_login


# Plan day IDs


def day_ids(plan: dict) -> dict[str, str]:
    return {d["name"]: d["id"] for d in plan["days"]}


def editor_data(trainee) -> dict:
    """What the editor page starts with for the trainee (edit_trainee_plan), day IDs included."""
    with SessionLocal() as session:
        return plan_editor.form_data(plans.of(session.get(Trainee, trainee.id)))


def test_a_plan_day_keeps_its_id_when_renamed_or_moved(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    before = day_ids(stored_plan(people["Kasia"]))
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan/edit").text
    assert f'data-day-id="{before["Wtorek – dół 1"]}"' in page  # the form carries the IDs
    # Rename the first day and move it to the end, as the editor sends it.
    data = editor_data(people["Kasia"])
    data["days"][0]["name"] = "Poniedziałek – klatka i plecy"
    data["days"].append(data["days"].pop(0))
    assert save(client, people["Kasia"], json.dumps(data)).status_code == 303
    after = day_ids(stored_plan(people["Kasia"]))
    assert after["Poniedziałek – klatka i plecy"] == before["Poniedziałek – góra 1"]
    assert after["Wtorek – dół 1"] == before["Wtorek – dół 1"]
    assert list(after)[-1] == "Poniedziałek – klatka i plecy"


def test_a_new_day_gets_a_new_id_and_a_day_added_again_its_old_one(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    before = day_ids(stored_plan(people["Kasia"]))
    data = editor_data(people["Kasia"])
    # "Wtorek – dół 1" deleted and added again (no ID, as a day added in the editor), and a
    # day that's new.
    tuesday = data["days"].pop(1)
    data["days"].append({**tuesday, "id": ""})
    new_day = {"id": "", "name": "Sobota – cardio", "exercises": [{"id": "running"}]}
    data["days"].append(new_day)
    assert save(client, people["Kasia"], json.dumps(data)).status_code == 303
    after = day_ids(stored_plan(people["Kasia"]))
    assert after["Wtorek – dół 1"] == before["Wtorek – dół 1"]
    assert after["Sobota – cardio"] not in before.values()


def test_a_tampered_day_id_is_an_error(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    data = editor_data(people["Kasia"])
    data["days"][1]["id"] = data["days"][0]["id"]  # two days with one ID
    response = save(client, people["Kasia"], json.dumps(data))
    assert response.status_code == 422
    assert "Nie udało się rozpoznać dnia, odśwież stronę" in response.text
    data["days"][1]["id"] = "<script>"
    response = save(client, people["Kasia"], json.dumps(data))
    assert response.status_code == 422
    assert "Dzień 2 „Wtorek – dół 1”: nie udało się rozpoznać dnia" in response.text


def test_library_plans_pass_their_day_ids_on(client, settings, people):
    sign_in(client, people["Trener Marek"])
    response = client.post(
        "/dashboard/plans/new",
        data={"name": "FBW", "plan": library_json("Trening 1")},
        follow_redirects=False,
    )
    template_id = int(response.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as session:
        library = day_ids(session.get(PlanTemplate, template_id).plan)
    # Edited in the library: the day keeps its ID (the editor sends it back).
    data = json.loads(library_json("Trening A"))
    data["days"][0]["id"] = library["Trening 1"]
    client.post(
        f"/dashboard/plans/{template_id}/edit", data={"name": "FBW", "plan": json.dumps(data)}
    )
    # Assigned to two trainees: both get the library plan's day IDs.
    for trainee in (people["Kasia"], people["Tomek"]):
        client.post(
            f"/dashboard/trainees/{trainee.id}/plan/assign", data={"template_id": template_id}
        )
        assert day_ids(stored_plan(trainee)) == {"Trening A": library["Trening 1"]}


# Treningi


def ex(exercise_id, name, in_plan=True, **fields) -> dict:
    """An exercise as the model returns it."""
    empty = dict(sets=None, reps=None, weight_kg=None, replaces=None, comment=None)
    return {"exercise_id": exercise_id, "name": name, "in_plan": in_plan, **empty, **fields}


def add_training(trainee, day, text, plan_day=None, exercises=(), status="done", notes=None):
    """A training report as the bot saves it: the model's answer checked against the trainee's
    plan (validate_output() works out the missed exercises and partial), then entries.save()."""
    with SessionLocal() as session:
        trainee = session.get(Trainee, trainee.id)
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
        training = {
            "date": day.isoformat(),
            "plan_day": plan_day,
            "status": status,
            "exercises": list(exercises),
            "notes": notes,
        }
        raw = json.dumps({"reply": "Zapisałem.", "weight": None, "training": training})
        output = llm_output.validate_output(raw, day, plans.of(trainee))
        entries.save(session, trainee, message, output)


def october(day: int) -> dt.date:
    return dt.date(2026, 10, day)


UPPER_1 = [
    ex("bench_press", "Wyciskanie sztangi na ławce płaskiej", sets=4, reps="8", weight_kg=62.5),
    ex("dumbbell_row", "Wiosłowanie hantlem", in_plan=False, replaces="barbell_row"),
    ex("overhead_press", "Wyciskanie żołnierskie"),
    ex("lat_pulldown", "Ściąganie drążka"),
    ex("biceps_curl", "Uginanie ramion"),
]
NAME = {i: e.name for i, e in exercises.BY_ID.items()}


def test_the_trainings_page_shows_the_month_and_each_days_trainings(client, settings, people):
    kasia, ola = people["Kasia"], people["Ola"]
    give_plan(kasia)
    add_training(kasia, october(5), "yanek góra 1 zrobiona", "Poniedziałek – góra 1", UPPER_1)
    lower = [ex("back_squat", "Przysiad"), ex("romanian_deadlift", "RDL")]
    add_training(kasia, october(6), "yanek dół, tylko przysiad i rdl", "Wtorek – dół 1", lower)
    run = [ex(None, "Bieg 5 km", in_plan=False, reps="28 min")]
    add_training(kasia, october(6), "yanek pobiegałem 5 km", exercises=run)
    skipped = dict(status="skipped", notes="ból barku")
    add_training(kasia, october(8), "yanek dziś bez", "Czwartek – góra 2", **skipped)
    add_training(kasia, october(9), "yanek martwy", "Piątek – dół 2", [ex("deadlift", "Martwy")])
    friday = ["deadlift", "bulgarian_split_squat", "leg_extension", "hip_thrust", "plank"]
    everything = [ex(i, NAME[i]) for i in friday]
    add_training(kasia, october(9), "yanek sorki, wszystko z dołu 2", "Piątek – dół 2", everything)
    add_training(ola, october(6), "yanek ola biegała", exercises=run)

    sign_in(client, people["Trener Marek"])
    response = client.get(f"/dashboard/trainees/{kasia.id}/trainings?month=2026-10")
    assert response.status_code == 200
    page = response.text
    assert "październik 2026" in page
    days = response.context["days"]
    assert [d.date.day for d in days] == [9, 8, 6, 5]  # newest first
    marks = {d.date.day: [(w.label, w.kind, w.mark) for w in d.workouts] for d in days}
    assert marks == {
        5: [("Pn", "done", "✓")],  # the replacement counts as done
        6: [("Wt", "partial", "2/4"), (None, "outside", "+")],  # a run besides Tuesday's day
        8: [("Cz", "skipped", "✕")],
        9: [("Pt", "done", "✓")],  # the correction counts
    }
    assert [w.reports for w in days[0].workouts] == [2]
    assert response.context["counts"] == {"done": 2, "partial": 1, "skipped": 1, "outside": 1}
    week = next(w for w in response.context["weeks"] if w.days[0].date == october(5))
    assert week.from_plan == 3

    # The cell says it in words too, and links to the day's details.
    assert (
        'aria-label="wt 6 paź 2026: Wt · Wtorek – dół 1: częściowo, 2 z 4 ćwiczeń; '
        'Poza planem: Bieg 5 km"' in page
    )
    assert 'href="#d-2026-10-06"' in page and 'id="d-2026-10-06"' in page
    assert f"Niezrobione: {NAME['leg_curl']}, {NAME['calf_raise']}" in page
    assert f"zamiast: {NAME['barbell_row']}" in page and "62,5 kg" in page
    assert "„yanek pobiegałem 5 km”" in page and "ból barku" in page
    assert page.count(">poprawiony<") == 1
    assert "2 pełne, 1 częściowy" in page
    assert "„yanek martwy”" not in page  # the corrected report
    assert "ola biegała" not in page  # another trainee's
    assert "<strong>Pn</strong> Poniedziałek – góra 1" in page  # the legend: the current plan


def test_a_days_label_follows_the_current_plan(client, settings, people):
    kasia = people["Kasia"]
    give_plan(kasia)
    add_training(kasia, october(5), "yanek góra 1", "Poniedziałek – góra 1", UPPER_1)
    lower = [ex("back_squat", "Przysiad")]
    add_training(kasia, october(6), "yanek dół 1", "Wtorek – dół 1", lower)
    # The trainer renames the Monday and gives it another label, puts a new day (no label)
    # first and drops the Tuesday.
    with SessionLocal() as session:
        trainee = session.get(Trainee, kasia.id)
        plan = plans.of(trainee)
        plan.days[0].name = "Poniedziałek – push"
        plan.days[0].label = "Pu"
        cardio = plans.PlanDay(name="Niedziela – cardio", exercises=[{"id": "plank"}])
        plan.days = [cardio, plan.days[0], *plan.days[2:]]
        plan_history.assign(session, trainee, plan, "edited")
        session.commit()

    sign_in(client, people["Trener Marek"])
    response = client.get(f"/dashboard/trainees/{kasia.id}/trainings?month=2026-10")
    monday, tuesday = (d.workouts[0] for d in reversed(response.context["days"]))
    # Monday's day is still in the plan (by its ID): the label it has there, and its new name.
    assert (monday.label, monday.current_name) == ("Pu", "Poniedziałek – push")
    assert "W obecnym planie: Poniedziałek – push" in response.text
    # Tuesday's day isn't: the label it had in the plan it was saved against.
    assert (tuesday.label, tuesday.current_name) == ("Wt", None)
    assert "<strong>A</strong> Niedziela – cardio" in response.text


def test_the_trainings_page_moves_between_months(client, settings, people):
    kasia = people["Kasia"]
    give_plan(kasia)
    lower = [ex("back_squat", "Przysiad")]
    add_training(kasia, dt.date(2026, 9, 29), "yanek dół 1", "Wtorek – dół 1", lower)
    add_training(kasia, october(5), "yanek góra 1", "Poniedziałek – góra 1", UPPER_1)
    sign_in(client, people["Trener Marek"])
    url = f"/dashboard/trainees/{kasia.id}/trainings"

    response = client.get(f"{url}?month=2026-10")
    # October's first week starts in September: the day is shown, links to September's page
    # and counts for the week, but it isn't one of October's trainings.
    first_week = response.context["weeks"][0]
    assert first_week.days[0].date == dt.date(2026, 9, 28) and first_week.from_plan == 1
    assert 'href="?month=2026-09#d-2026-09-29"' in response.text
    assert [d.date for d in response.context["days"]] == [october(5)]
    assert 'href="?month=2026-09"' in response.text

    # Without a month (or with something that isn't one): this month, in the trainee's time.
    this_month = dt.datetime.now(ZoneInfo(kasia.timezone)).date().replace(day=1)
    for query in ("", "?month=abc", "?month=2026-13"):
        assert client.get(url + query).context["month"] == this_month, query
    if this_month > october(1):
        page = client.get(url).text
        assert "nie ma zgłoszonych treningów" in page
        assert 'href="?month=2026-10#d-2026-10-05"' in page  # the last training
        assert response.context["next"] == "2026-11"
    # No month after this one.
    assert client.get(url).context["next"] is None


def test_the_trainings_page_without_trainings(client, settings, people):
    sign_in(client, people["Trener Marek"])
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/trainings").text
    assert "Brak zgłoszonych treningów" in page and "calendar" not in page
