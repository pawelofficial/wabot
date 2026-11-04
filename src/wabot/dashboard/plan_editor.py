"""The training plan editor: what the form shows and how a submitted plan is checked.

The editor page (templates/plan_edit.html, static/plan_editor.js) lets the trainer add, remove
and reorder days and exercises. On "Zapisz" the script sends the whole plan as one JSON form
field, `plan`, shaped like plans.TrainingPlan. parse() tidies it up (trims text, drops empty
fields), checks it with TrainingPlan (the same rules as a YAML plan) and turns the errors into
Polish. With errors, the page is shown again with what the trainer typed.

Each day carries its ID (plans.PlanDay.id) through the form, unseen, so a renamed or moved day
stays the same day; a day added in the editor has none until the plan is saved.
"""

import json
from dataclasses import dataclass

from pydantic import ValidationError

from wabot import exercises
from wabot.plans import TrainingPlan

TEXT_FIELDS = ("sets", "reps", "weight", "note")


@dataclass
class Group:
    name: str  # "nogi"
    options: list[tuple[str, str]]  # (ID, Polish name)


def exercise_groups() -> list[Group]:
    """The catalog for the exercise dropdowns, by muscle group, in catalog order."""
    groups: dict[str, Group] = {}
    for e in exercises.CATALOG:
        groups.setdefault(e.group, Group(e.group, [])).options.append((e.id, e.name))
    return list(groups.values())


def form_data(plan: TrainingPlan | None) -> dict:
    """What the editor starts with: the trainee's plan, or one empty day for a new plan."""
    if plan is None:
        return {"notes": "", "days": [{"id": "", "name": "", "exercises": [_exercise({})]}]}
    return _clean(plan.model_dump(mode="json"))


def parse(text: str) -> tuple[dict, TrainingPlan | None, list[str]]:
    """The submitted plan JSON: (the form data to show again, the plan or None, the errors)."""
    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        return form_data(None), None, ["Nie udało się odczytać planu. Odśwież stronę."]
    data = _clean(raw)
    try:
        return data, TrainingPlan.model_validate(_without_empty(data)), []
    except ValidationError as e:
        return data, None, _messages(e, data)


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _exercise(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    exercise = {"id": _text(raw.get("id"))}
    exercise |= {field: _text(raw.get(field)) for field in TEXT_FIELDS}
    exercise["replacements"] = [_text(r) for r in _list(raw.get("replacements")) if _text(r)]
    return exercise


def _clean(raw: dict) -> dict:
    """The submitted plan in the form's shape: every field present, text trimmed. Whatever the
    browser sent, the template can show it."""
    return {
        "notes": _text(raw.get("notes")),
        "days": [
            {
                "id": _text(day.get("id")),
                "name": _text(day.get("name")),
                "exercises": [_exercise(e) for e in _list(day.get("exercises"))],
            }
            for day in _list(raw.get("days"))
            if isinstance(day, dict)
        ],
    }


def _without_empty(data: dict) -> dict:
    """For TrainingPlan: an empty field is a field the trainer didn't fill in."""
    return {
        "notes": data["notes"] or None,
        "days": [
            {
                "id": day["id"] or None,
                "name": day["name"],
                "exercises": [
                    {key: value for key, value in e.items() if value != ""}
                    for e in day["exercises"]
                ],
            }
            for day in data["days"]
        ],
    }


def _exercise_name(exercise_id) -> str:
    catalog = exercises.BY_ID.get(exercise_id)
    return catalog.name if catalog else str(exercise_id)


def _place(loc: tuple, data: dict) -> str:
    """Where an error is, as the trainer sees it: "Dzień 2 „Wtorek”, ćwiczenie 3 (Plank)"."""
    parts = []
    if len(loc) >= 2 and loc[0] == "days" and isinstance(loc[1], int):
        day = data["days"][loc[1]]
        parts.append(f"Dzień {loc[1] + 1}" + (f" „{day['name']}”" if day["name"] else ""))
        if len(loc) >= 4 and loc[2] == "exercises" and isinstance(loc[3], int):
            exercise = day["exercises"][loc[3]]
            name = exercises.BY_ID.get(exercise["id"])
            parts.append(f"ćwiczenie {loc[3] + 1}" + (f" ({name.name})" if name else ""))
    return ", ".join(parts)


def _message(error: dict) -> str:
    kind, loc, context = error["type"], error["loc"], error.get("ctx") or {}
    field = loc[-1] if loc else None
    if kind == "duplicate_day_id" or (len(loc) == 3 and loc[0] == "days" and field == "id"):
        # Only a tampered or very stale form gets here: the IDs come from the server.
        return "nie udało się rozpoznać dnia, odśwież stronę"
    if kind == "duplicate_day":
        return f"dwa dni mają tę samą nazwę („{context['name']}”)"
    if kind == "duplicate_exercise":
        return f"{_exercise_name(context['id'])} jest w tym dniu dwa razy"
    if kind == "own_replacement":
        return "ćwiczenie nie może być swoim własnym zamiennikiem"
    if kind == "duplicate_replacement":
        return "ten sam zamiennik jest dodany dwa razy"
    if field == "days" and kind == "too_short":
        return "plan musi mieć co najmniej jeden dzień"
    if field == "exercises" and kind == "too_short":
        return "dodaj co najmniej jedno ćwiczenie"
    if field == "name":
        return "podaj nazwę dnia"
    if field == "id":
        return "wybierz ćwiczenie" if kind == "missing" else "nieznane ćwiczenie"
    if "replacements" in loc:
        return "nieznany zamiennik"
    if field == "sets":
        return "liczba serii musi być liczbą całkowitą większą od zera"
    return error["msg"]


def _messages(error: ValidationError, data: dict) -> list[str]:
    messages = []
    for e in error.errors():
        place = _place(e["loc"], data)
        text = _message(e)
        line = f"{place}: {text}" if place else text[0].upper() + text[1:]
        if line not in messages:
            messages.append(line)
    return messages
