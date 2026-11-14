"""Trenerzy: the admins' page (ADMIN_EMAILS) for adding trainers. An admin enters a trainer's
name and Google email; the trainer can then sign in with that Google account (there's no
self-service sign-up: each trainer adds OpenAI costs and their trainees' data, docs/PLAN.md).
"""

import logging
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from wabot import trainees
from wabot.dashboard.auth import AdminDep, SessionDep
from wabot.dashboard.routes import render
from wabot.models import Trainee, Trainer

log = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", default_response_class=HTMLResponse)

NAME_MAX = 100  # trainers.name


@dataclass
class NewTrainer:
    name: str = ""
    email: str = ""
    errors: dict[str, str] = field(default_factory=dict)


def check(session, form: NewTrainer) -> NewTrainer:
    """Fill in form.errors (by field, in Polish); form.email becomes the stored form."""
    form.name = form.name.strip()
    if not form.name:
        form.errors["name"] = "Podaj imię i nazwisko trenera."
    elif len(form.name) > NAME_MAX:
        form.errors["name"] = f"Najwyżej {NAME_MAX} znaków."
    elif session.scalar(select(Trainer).where(Trainer.name == form.name)) is not None:
        form.errors["name"] = "Trener o tej nazwie już jest."
    try:
        email = trainees.normalize_email(form.email)
    except ValueError:
        form.errors["email"] = "Podaj adres Gmail, którym trener będzie się logować."
    else:
        taken = session.scalar(select(Trainer).where(Trainer.email == email))
        if taken is not None:
            form.errors["email"] = f"Tym adresem loguje się już {taken.name}."
        else:
            form.email = email
    return form


def trainers_page(request, session, admin, form: NewTrainer, added=None, status=200):
    counts = dict(
        session.execute(select(Trainee.trainer_id, func.count()).group_by(Trainee.trainer_id)).all()
    )
    rows = [
        (t, counts.get(t.id, 0)) for t in session.scalars(select(Trainer).order_by(Trainer.name))
    ]
    response = render(
        request,
        "trainers.html",
        session,
        admin,
        section="trainers",
        rows=rows,
        form=form,
        added=added,
    )
    response.status_code = status
    return response


@router.get("/trainers")
def trainers(request: Request, session: SessionDep, admin: AdminDep, added: str | None = None):
    new = session.get(Trainer, int(added)) if added and added.isdigit() else None
    return trainers_page(request, session, admin, NewTrainer(), added=new)


@router.post("/trainers")
def add_trainer(
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
):
    form = check(session, NewTrainer(name, email))
    if form.errors:
        return trainers_page(request, session, admin, form, status=422)
    trainer = Trainer(name=form.name, email=form.email)
    session.add(trainer)
    session.commit()
    log.info("Admin %s added trainer %s (%s)", admin.id, trainer.id, trainer.name)
    return RedirectResponse(f"/dashboard/trainers?added={trainer.id}", 303)
