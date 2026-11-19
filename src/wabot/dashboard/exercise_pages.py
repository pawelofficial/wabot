"""Baza ćwiczeń: the exercises a trainer can put in plans, and adding one the built-in catalog
doesn't have (custom_exercises.py). The plan editor adds one without leaving the page: it posts
the same form with Accept: application/json and gets the new exercise (or the errors) back.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from wabot import custom_exercises, exercises
from wabot.custom_exercises import NewExercise
from wabot.dashboard.auth import SessionDep, TrainerDep
from wabot.dashboard.routes import render
from wabot.models import CustomExercise

router = APIRouter(prefix="/dashboard", default_response_class=HTMLResponse)


def exercises_page(request, session, trainer, form: NewExercise, added=None, status=200):
    own = session.scalars(
        select(CustomExercise)
        .where(CustomExercise.trainer_id == trainer.id)
        .order_by(CustomExercise.name)
    ).all()
    built_in: dict[str, list[exercises.CatalogExercise]] = {}
    for e in exercises.CATALOG:
        built_in.setdefault(e.group, []).append(e)
    response = render(
        request,
        "exercises.html",
        session,
        trainer,
        section="exercises",
        own=own,
        built_in=built_in,
        groups=exercises.GROUPS,
        form=form,
        added=added,
    )
    response.status_code = status
    return response


@router.get("/exercises")
def exercise_list(
    request: Request, session: SessionDep, trainer: TrainerDep, added: str | None = None
):
    new = session.scalar(
        select(CustomExercise).where(
            CustomExercise.exercise_id == added, CustomExercise.trainer_id == trainer.id
        )
    )
    return exercises_page(request, session, trainer, NewExercise(), added=new)


@router.post("/exercises")
def add_exercise(
    request: Request,
    session: SessionDep,
    trainer: TrainerDep,
    name: Annotated[str, Form()] = "",
    group: Annotated[str, Form()] = "",
    aliases: Annotated[str, Form()] = "",
):
    wants_json = "application/json" in request.headers.get("accept", "")
    form = custom_exercises.check(session, trainer, NewExercise(name, group, aliases))
    if form.errors:
        if wants_json:
            return JSONResponse({"errors": form.errors}, 422)
        return exercises_page(request, session, trainer, form, status=422)
    row = custom_exercises.add(session, trainer, form)
    if wants_json:
        return JSONResponse({"id": row.exercise_id, "name": row.name, "group": row.group})
    return RedirectResponse(f"/dashboard/exercises?added={row.exercise_id}", 303)
