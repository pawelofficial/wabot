"""Baza ćwiczeń: a trainer adds an exercise the built-in catalog doesn't have; it's theirs only,
usable in their plans, and the bot recognises it in reports."""

# The fixtures shared with test_dashboard.py are imported, then used as test arguments.
# ruff: noqa: F401, F811

import datetime as dt
import json

import pytest

from wabot import exercises, llm_output
from wabot.db import SessionLocal
from wabot.models import CustomExercise
from wabot.plans import TrainingPlan

from .test_dashboard import edited, people, save, settings, sign_in, stored_plan

FLY = {
    "name": "Wznosy hantli w opadzie",
    "group": "barki",
    "aliases": "odwrotne rozpiętki, Reverse fly",
}
FLY_ID = "custom_wznosy_hantli_w_opadzie"


def add(client, json_reply=False, **fields):
    headers = {"Accept": "application/json"} if json_reply else {}
    return client.post(
        "/dashboard/exercises", data={**FLY, **fields}, headers=headers, follow_redirects=False
    )


def test_a_trainer_adds_an_exercise(client, settings, people):
    sign_in(client, people["Trener Marek"])
    response = add(client)
    assert response.status_code == 303
    assert response.headers["location"] == f"/dashboard/exercises?added={FLY_ID}"
    page = client.get(response.headers["location"]).text
    assert "Dodano: Wznosy hantli w opadzie." in page
    assert "odwrotne rozpiętki, Reverse fly" in page
    with SessionLocal() as session:
        row = session.query(CustomExercise).one()
    assert (row.trainer_id, row.group) == (people["Trener Marek"].id, "barki")
    assert row.aliases == ["odwrotne rozpiętki", "Reverse fly"]


def test_only_its_trainer_sees_it_in_the_plan_editor(client, settings, people):
    sign_in(client, people["Trener Marek"])
    add(client)
    editor = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan/edit").text
    assert f'value="{FLY_ID}"' in editor
    assert "Wznosy hantli w opadzie" in client.get("/dashboard/exercises").text
    sign_in(client, people["Trenerka Ewa"])
    editor = client.get(f"/dashboard/trainees/{people['Ola'].id}/plan/edit").text
    assert FLY_ID not in editor
    assert "Wznosy hantli w opadzie" not in client.get("/dashboard/exercises").text


@pytest.mark.parametrize(
    ("fields", "field", "message"),
    [
        ({"name": "  "}, "name", "Podaj nazwę ćwiczenia."),
        ({"name": "PRZYSIAD"}, "name", "To ćwiczenie już jest w bazie: Przysiad ze sztangą."),
        ({"group": "nogi i ręce"}, "group", "Wybierz partię mięśni."),
        ({"aliases": "siady, coś"}, "aliases", "„siady” to już Przysiad ze sztangą"),
    ],
)
def test_a_name_already_taken_or_a_missing_field_is_refused(
    client, settings, people, fields, field, message
):
    sign_in(client, people["Trener Marek"])
    response = add(client, **fields)
    assert response.status_code == 422 and message in response.text
    response = add(client, json_reply=True, **fields)
    assert response.status_code == 422 and message in response.json()["errors"][field]
    with SessionLocal() as session:
        assert session.query(CustomExercise).count() == 0


def test_the_same_trainer_cant_add_it_twice_another_trainer_can(client, settings, people):
    sign_in(client, people["Trener Marek"])
    add(client)
    assert "już jest w bazie" in add(client, name="reverse fly", aliases="").text
    sign_in(client, people["Trenerka Ewa"])
    response = add(client, json_reply=True)
    # Ewa's own copy, under its own permanent ID.
    assert response.json() == {
        "id": f"{FLY_ID}_2",
        "name": "Wznosy hantli w opadzie",
        "group": "barki",
    }


def test_the_plan_editor_adds_one_as_json(client, settings, people):
    sign_in(client, people["Trener Marek"])
    response = add(client, json_reply=True, name="Ściąganie  łokci", aliases="")
    assert response.status_code == 200
    assert response.json()["id"] == "custom_sciaganie_lokci"
    assert response.json()["name"] == "Ściąganie łokci"


def test_a_plan_with_it_saves_but_not_with_another_trainers(client, settings, people):
    sign_in(client, people["Trener Marek"])
    add(client)
    response = save(client, people["Kasia"], edited(days__0__exercises__0__id=FLY_ID))
    assert response.status_code == 303
    assert stored_plan(people["Kasia"])["days"][0]["exercises"][0]["id"] == FLY_ID
    page = client.get(f"/dashboard/trainees/{people['Kasia'].id}/plan").text
    assert "Wznosy hantli w opadzie" in page

    sign_in(client, people["Trenerka Ewa"])
    response = save(client, people["Ola"], edited(days__0__exercises__0__id=FLY_ID))
    assert response.status_code == 422
    assert "spoza Twojej bazy ćwiczeń" in response.text
    assert stored_plan(people["Ola"]) is None


def test_an_exercise_added_elsewhere_is_known_on_the_next_request(client, settings, people):
    """The bot and the dashboard are separate processes locally: each request reloads them."""
    with SessionLocal() as session:
        session.add(
            CustomExercise(
                trainer_id=people["Trener Marek"].id,
                exercise_id="custom_plank_z_unoszeniem",
                name="Plank z unoszeniem nogi",
                aliases=[],
                group="brzuch",
            )
        )
        session.commit()
    assert "custom_plank_z_unoszeniem" not in exercises.BY_ID
    sign_in(client, people["Trener Marek"])
    assert "Plank z unoszeniem nogi" in client.get("/dashboard/exercises").text
    assert exercises.BY_ID["custom_plank_z_unoszeniem"].trainer_id == people["Trener Marek"].id


def own_exercise(trainer_id) -> None:
    exercises.set_custom(
        [
            exercises.CatalogExercise(
                "custom_wznosy",
                "Wznosy hantli w opadzie",
                ("odwrotne rozpiętki",),
                "barki",
                trainer_id,
            )
        ]
    )


def test_the_llm_schema_and_prompt_have_the_trainers_own_exercises():
    own_exercise(7)
    schema = json.dumps(llm_output.json_schema(exercises.ids_of_trainer(7)))
    assert '"custom_wznosy"' in schema and '"back_squat"' in schema
    assert exercises.SCHEMA_PLACEHOLDER not in schema
    assert "custom_wznosy" not in json.dumps(llm_output.json_schema(exercises.ids_of_trainer(8)))
    plan = TrainingPlan.model_validate(
        {"days": [{"name": "A", "exercises": [{"id": "custom_wznosy", "sets": 3}]}]}
    )
    assert "custom_wznosy: Wznosy hantli w opadzie (też: odwrotne rozpiętki)" in plan.prompt_text()


def test_a_report_matches_the_trainers_own_exercise_by_name():
    own_exercise(7)
    exercise = {
        "exercise_id": None,
        "name": "Odwrotne rozpiętki",
        "in_plan": False,
        "sets": 3,
        "reps": "12",
        "weight_kg": None,
        "replaces": None,
        "comment": None,
    }
    training = {
        "date": "2026-11-02",
        "plan_day": None,
        "status": "done",
        "exercises": [exercise],
        "notes": None,
    }
    report = json.dumps({"reply": "Zanotowałem.", "weight": None, "training": training})
    today = dt.date(2026, 11, 3)
    found = llm_output.validate_output(report, today, None, trainer_id=7)
    assert found.training.exercises[0].exercise_id == "custom_wznosy"
    # Another trainer's exercise isn't matched.
    other = llm_output.validate_output(report, today, None, trainer_id=8)
    assert other.training.exercises[0].exercise_id is None


def test_an_unknown_id_is_still_refused():
    with pytest.raises(ValueError, match="unknown exercise ID"):
        TrainingPlan.model_validate({"days": [{"name": "A", "exercises": [{"id": "custom_nic"}]}]})
