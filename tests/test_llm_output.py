import datetime as dt
import json
from types import SimpleNamespace

import pytest

from wabot import assistant, exercises, llm_output, plans
from wabot.config import Settings
from wabot.llm_output import InvalidOutputError, validate_output
from wabot.models import DEFAULT_TIMEZONE, Trainee, utcnow
from wabot.plans import TrainingPlan

TODAY = dt.date(2026, 11, 3)
PLAN = TrainingPlan.model_validate(
    {
        "days": [
            {
                "name": "Dzień A – poniedziałek (dół ciała)",
                "exercises": [
                    {
                        "id": "back_squat",
                        "sets": 4,
                        "reps": "6–8",
                        "weight": "ok. 60 kg",
                        "replacements": ["leg_press"],
                    },
                    {"id": "hip_thrust", "sets": 3, "reps": "10–12", "weight": "70 kg"},
                ],
            },
            {"name": "Dzień B – środa", "exercises": [{"id": "bench_press"}]},
        ]
    }
)
TRAINEE = Trainee(training_plan=plans.to_db(PLAN), timezone=DEFAULT_TIMEZONE)


def output(**overrides) -> dict:
    data = {"reply": "Zanotowałem.", "weight": None, "training": None}
    return {**data, **overrides}


def training(**overrides) -> dict:
    data = {
        "date": "2026-11-02",
        "plan_day": "Dzień A – poniedziałek (dół ciała)",
        "status": "partial",
        "exercises": [
            {
                "exercise_id": "back_squat",
                "name": "Przysiad ze sztangą",
                "in_plan": True,
                "sets": 4,
                "reps": "8",
                "weight_kg": 60,
                "replaces": None,
                "comment": None,
            }
        ],
        "notes": None,
    }
    return {**data, **overrides}


def exercise(exercise_id: str | None, **fields) -> dict:
    data = {
        "exercise_id": exercise_id,
        "name": "Ćwiczenie",
        "in_plan": True,
        "sets": None,
        "reps": None,
        "weight_kg": None,
        "replaces": None,
        "comment": None,
    }
    return {**data, **fields}


def errors_of(data: dict, plan: TrainingPlan | None = PLAN) -> list[str]:
    with pytest.raises(InvalidOutputError) as e:
        validate_output(json.dumps(data), TODAY, plan)
    return e.value.errors


def test_valid_output_is_parsed():
    data = output(weight={"kg": 82.4, "date": "2026-11-03"}, training=training())
    result = validate_output(json.dumps(data), TODAY, PLAN)
    assert result.weight.kg == 82.4
    assert result.weight.date == TODAY
    assert result.training.exercises[0].name == "Przysiad ze sztangą"


def test_polish_letters_emoji_and_formatting_are_fine():
    reply = "Zażółć gęślą jaźń 💪 *Dzień A* – 4 x 6–8, 60 kg. Café ✅"
    assert validate_output(json.dumps(output(reply=reply)), TODAY, None).reply == reply


def test_nothing_reported_is_valid():
    assert validate_output(json.dumps(output()), TODAY, None).weight is None


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ("not json", "Invalid JSON"),
        ({"reply": "hej"}, "weight: Field required"),
        (output(extra=1), "extra: Extra inputs are not permitted"),
        (output(weight={"kg": "dużo", "date": "2026-11-03"}), "weight.kg"),
        (output(weight={"kg": 82, "date": "2026-13-45"}), "weight.date"),
        (output(training=training(status="maybe")), "training.status"),
    ],
)
def test_wrong_structure_is_rejected(data, error):
    raw = data if isinstance(data, str) else json.dumps(data)
    with pytest.raises(InvalidOutputError) as e:
        validate_output(raw, TODAY, PLAN)
    assert any(error in msg for msg in e.value.errors), e.value.errors


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (output(reply="  "), "reply is empty"),
        (output(reply='{"reply": "x"}'), "not JSON"),
        (output(reply="Super. Եթե chcesz, rozpiszę środę."), "non-Polish letters 'Եեթ'"),
        (output(reply="Записал 82 kg."), "non-Polish letters"),
        (output(reply="x" * 4097), "WhatsApp allows 4096"),
        (output(weight={"kg": 8.24, "date": "2026-11-03"}), "outside 30–300 kg"),
        (output(weight={"kg": 82, "date": "2026-11-04"}), "in the future"),
        (output(weight={"kg": 82, "date": "2026-09-01"}), "more than 31 days ago"),
        (output(training=training(status="skipped")), "skipped but exercises are listed"),
        (output(training=training(exercises=[])), "no exercises are listed"),
        (
            output(training=training(plan_day="Dzień Z")),
            "plan_day 'Dzień Z' is not a day of the plan",
        ),
        (
            output(training=training(exercises=[exercise("bench_press", sets=30)])),
            "sets 30 is outside",
        ),
        (
            output(training=training(exercises=[exercise("bench_press")])),
            "in_plan is true but bench_press isn't in 'Dzień A – poniedziałek (dół ciała)'",
        ),
        (
            output(training=training(exercises=[exercise(None)])),
            "in_plan is true, so exercise_id must be the plan's ID",
        ),
        (
            output(training=training(exercises=[exercise("back_squat", in_plan=False)])),
            "so in_plan must be true",
        ),
    ],
)
def test_implausible_values_are_rejected(data, error):
    errors = errors_of(data)
    assert any(error in msg for msg in errors), errors


def test_all_problems_are_reported_at_once():
    data = output(reply="", weight={"kg": 900, "date": "2026-11-03"})
    assert len(errors_of(data)) == 2


def test_plan_day_is_matched_ignoring_case_and_spacing():
    data = output(training=training(plan_day="dzień a –  poniedziałek (dół ciała)"))
    result = validate_output(json.dumps(data), TODAY, PLAN)
    assert result.training.plan_day == "Dzień A – poniedziałek (dół ciała)"


def test_without_a_day_any_plan_exercise_is_allowed():
    data = output(training=training(plan_day=None, exercises=[exercise("bench_press")]))
    result = validate_output(json.dumps(data), TODAY, PLAN).training
    # No plan day, so nothing counts as missed.
    assert (result.plan_day, result.missed_exercises) == (None, [])


@pytest.mark.parametrize(
    ("status", "exercise_ids", "replaced", "expected_status", "expected_missed"),
    [
        # "zrobiłem tylko przysiady": the rest of the day wasn't reported, so it wasn't done.
        ("done", ["back_squat"], [], "partial", ["hip_thrust"]),
        ("partial", ["back_squat", "hip_thrust"], [], "done", []),
        # A replacement covers the exercise it replaces.
        ("done", ["hip_thrust"], ["back_squat"], "done", []),
        ("skipped", [], [], "skipped", ["back_squat", "hip_thrust"]),
    ],
)
def test_missed_exercises_and_status_come_from_the_plan_day(
    status, exercise_ids, replaced, expected_status, expected_missed
):
    done = [exercise(i) for i in exercise_ids]
    done += [exercise("leg_press", in_plan=False, replaces=r) for r in replaced]
    data = output(training=training(status=status, exercises=done))
    result = validate_output(json.dumps(data), TODAY, PLAN).training
    assert (result.status, result.missed_exercises) == (expected_status, expected_missed)


def test_missed_exercises_are_not_in_the_schema():
    assert "missed_exercises" not in llm_output.json_schema()["$defs"]["Training"]["properties"]


def test_without_a_plan_nothing_may_claim_to_be_in_it():
    errors = errors_of(output(training=training()), plan=None)
    assert any("plan_day must be null" in msg for msg in errors)
    assert any("in_plan must be false" in msg for msg in errors)


def test_schema_is_valid_for_strict_mode():
    schema = llm_output.json_schema()

    def check(node):
        if node.get("type") == "object":
            assert node["required"] == list(node["properties"])
            assert node["additionalProperties"] is False
        for sub in [*node.get("properties", {}).values(), *node.get("anyOf", [])]:
            check(sub)
        if "items" in node:
            check(node["items"])

    check(schema)
    for definition in schema["$defs"].values():
        check(definition)


class FakeOpenAI:
    """Returns the given raw outputs one per call and records each call's input."""

    def __init__(self, outputs: list[str]):
        self.outputs = outputs
        self.inputs = []
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.inputs.append(kwargs["input"])
        return SimpleNamespace(output=[], output_text=self.outputs.pop(0))


@pytest.fixture
def fake_openai(monkeypatch):
    def install(outputs):
        fake = FakeOpenAI(outputs)
        monkeypatch.setattr(assistant.openai, "OpenAI", lambda **kwargs: fake)
        return fake

    return install


def test_invalid_output_is_retried_with_the_errors(fake_openai):
    bad = json.dumps(output(weight={"kg": 824, "date": "2026-11-03"}))
    good = json.dumps(
        output(reply="Zanotowałem 82,4 kg.", weight={"kg": 82.4, "date": "2026-11-03"})
    )
    fake = fake_openai([bad, good])

    now = dt.datetime(2026, 11, 3, 10, tzinfo=dt.UTC)
    result = assistant.generate_reply(Settings(), TRAINEE, [], now)

    assert result.weight.kg == 82.4
    retry_input = fake.inputs[1]
    assert retry_input[-2] == {"role": "assistant", "content": bad}
    assert "outside 30–300 kg" in retry_input[-1]["content"]


def test_output_still_invalid_after_retry_raises(fake_openai):
    bad = json.dumps(output(reply=""))
    fake_openai([bad, bad])
    with pytest.raises(InvalidOutputError):
        assistant.generate_reply(Settings(), TRAINEE, [], utcnow())


def test_markdown_bold_becomes_whatsapp_bold():
    reply = "**Dzień A – poniedziałek**\nPrzysiad – 4 x 6–8, *60 kg*"
    result = validate_output(json.dumps(output(reply=reply)), TODAY, None)
    assert result.reply == "*Dzień A – poniedziałek*\nPrzysiad – 4 x 6–8, *60 kg*"


def substitution(**overrides) -> dict:
    suwnica = {
        "exercise_id": "leg_press",
        "name": "Suwnica na nogi",
        "in_plan": False,
        "sets": None,
        "reps": None,
        "weight_kg": None,
        "replaces": "back_squat",
        "comment": "Zamiast przysiadów.",
    }
    hip_thrust = {**suwnica, "exercise_id": "hip_thrust", "name": "Hip thrust", "in_plan": True}
    hip_thrust["replaces"] = None
    hip_thrust["comment"] = None
    defaults = {"status": "done", "exercises": [suwnica, hip_thrust]}
    return training(**{**defaults, **overrides})


def test_substituted_exercise_is_valid():
    result = validate_output(json.dumps(output(training=substitution())), TODAY, PLAN)
    suwnica = result.training.exercises[0]
    assert (suwnica.exercise_id, suwnica.replaces) == ("leg_press", "back_squat")


def test_replacement_the_trainer_didnt_list_is_valid():
    data = substitution()
    data["exercises"][0].update(exercise_id=None, name="Przysiad bułgarski")
    result = validate_output(json.dumps(output(training=data)), TODAY, PLAN)
    # Not among the plan's replacements, so the model leaves the ID out; the catalog has it.
    assert result.training.exercises[0].exercise_id == "bulgarian_split_squat"


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"replaces": "bench_press"}, "replaces bench_press, which isn't in 'Dzień A"),
        ({"in_plan": True}, "in_plan is true but leg_press isn't in"),
    ],
)
def test_bad_substitution_is_rejected(change, error):
    data = substitution()
    data["exercises"][0].update(change)
    errors = errors_of(output(training=data))
    assert any(error in msg for msg in errors), errors


def test_unknown_exercise_id_is_rejected():
    data = training()
    data["exercises"][0]["exercise_id"] = "squat_jump"
    errors = errors_of(output(training=data))
    assert any("exercise_id" in msg for msg in errors), errors


def test_exercise_outside_the_catalog_has_no_id():
    data = training()
    data["exercises"][0].update(exercise_id=None, name="Skakanka", in_plan=False)
    result = validate_output(json.dumps(output(training=data)), TODAY, PLAN)
    assert result.training.exercises[0].exercise_id is None


@pytest.mark.parametrize("plan", [PLAN, None])
def test_exercise_outside_the_plan_gets_its_id_from_the_catalog(plan):
    data = training(plan_day=None, status="done")
    data["exercises"][0].update(exercise_id=None, name="pompki", in_plan=False)
    result = validate_output(json.dumps(output(training=data)), TODAY, plan)
    assert result.training.exercises[0].exercise_id == "push_up"


@pytest.mark.parametrize(
    ("exercise_id", "name", "expected"),
    [
        # The model's ID for an exercise outside the plan is kept when the name isn't known...
        ("running", "Bieg 5 km", "running"),
        # ...and the catalog's match by name wins over it.
        ("cycling", "Bieganie", "running"),
    ],
)
def test_id_outside_the_plan_is_checked_against_the_name(exercise_id, name, expected):
    data = training(plan_day=None, status="done")
    data["exercises"][0].update(exercise_id=exercise_id, name=name, in_plan=False)
    result = validate_output(json.dumps(output(training=data)), TODAY, PLAN)
    assert result.training.exercises[0].exercise_id == expected


def test_schema_lists_the_catalog_ids():
    exercise = llm_output.json_schema()["$defs"]["Exercise"]["properties"]["exercise_id"]
    enum = next(option["enum"] for option in exercise["anyOf"] if "enum" in option)
    assert enum == list(exercises.IDS)
