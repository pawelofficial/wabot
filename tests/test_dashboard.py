"""The dashboard: signing in, that a trainer sees only their own trainees, and the pages."""

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from wabot import assistant, plan_history, plans
from wabot.config import Settings, get_settings
from wabot.dashboard import auth, plan_editor, queries, routes
from wabot.db import SessionLocal
from wabot.main import app
from wabot.models import Message, PlanTemplate, Trainee, TraineePlan, Trainer, WeightEntry

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
        return session.get(Trainee, trainee.id).training_plan


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
    assert page.count("data-day>") == 2  # the one day, and the template for "+ Dodaj dzień"


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
    assert stored_plan(people["Kasia"]) == {
        "days": [{"name": "A", "exercises": [{"id": "plank", "reps": "45 s"}]}]
    }


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


def test_a_bad_plan_is_shown_again_as_typed(client, settings, people):
    give_plan(people["Kasia"])
    sign_in(client, people["Trener Marek"])
    response = save(
        client, people["Kasia"], edited(days__1__name="", days__0__exercises__0__weight="82,5 kg")
    )
    assert response.status_code == 422
    assert 'value="82,5 kg"' in response.text


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
