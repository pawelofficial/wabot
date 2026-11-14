"""Adding a trainee (Dodaj podopiecznego): the trainer registers them with their name, phone
number and the date of the paper consent (docs/PLAN.md, "Trainee onboarding"), optionally with a
plan from the library. The trainee then opens the bot on Telegram and shares that number, which
connects the chat (main.receive_telegram()).
"""

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from wabot import plan_history, telegram, trainees
from wabot.config import Settings
from wabot.dashboard.auth import SessionDep, TrainerDep
from wabot.dashboard.routes import render
from wabot.models import DEFAULT_TIMEZONE, PlanTemplate, Trainee, Trainer
from wabot.plans import TrainingPlan

log = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", default_response_class=HTMLResponse)

NAME_MAX = 100  # trainees.name


def today() -> dt.date:
    return dt.datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()


def phone_from_form(text: str) -> str:
    """The number as trainees.phone stores it. A Polish number may be typed without the country
    code ("792 541 888"): nine digits get 48 in front. Raises ValueError."""
    digits = re.sub(r"[\s()+.-]", "", text)
    if re.fullmatch(r"\d{9}", digits) and not text.strip().startswith("+"):
        digits = "48" + digits
    return trainees.normalize_phone(digits)


@dataclass
class NewTrainee:
    """The form's values, as typed (shown again with the errors) and as they'll be saved."""

    name: str = ""
    phone: str = ""
    consent_date: str = ""
    template_id: str = ""
    errors: dict[str, str] = field(default_factory=dict)
    # Parsed, when there are no errors.
    phone_digits: str = ""
    consent: dt.date | None = None
    template: PlanTemplate | None = None


def check(session, trainer: Trainer, form: NewTrainee) -> NewTrainee:
    """Fill in form.errors (by field, in Polish) or the parsed values."""
    form.name = form.name.strip()
    if not form.name:
        form.errors["name"] = "Podaj imię podopiecznego."
    elif len(form.name) > NAME_MAX:
        form.errors["name"] = f"Imię może mieć najwyżej {NAME_MAX} znaków."

    try:
        form.phone_digits = phone_from_form(form.phone)
    except ValueError:
        form.errors["phone"] = "Podaj numer telefonu, np. 792 541 888 albo +48 792 541 888."
    else:
        taken = trainees.find_by_phone(session, form.phone_digits)
        if taken is not None and taken.trainer_id == trainer.id:
            form.errors["phone"] = f"Ten numer ma już {taken.name}."
        elif taken is not None:
            # Another trainer's trainee: not who.
            form.errors["phone"] = "Ten numer jest już zarejestrowany u innego trenera."

    try:
        form.consent = dt.date.fromisoformat(form.consent_date)
    except ValueError:
        form.errors["consent_date"] = "Podaj datę podpisania zgody."
    else:
        if form.consent > today():
            form.errors["consent_date"] = "Data zgody nie może być w przyszłości."
        elif form.consent.year < 2000:
            form.errors["consent_date"] = "Sprawdź datę zgody."

    if form.template_id:
        form.template = session.get(PlanTemplate, int(form.template_id))
        if form.template is None or form.template.trainer_id != trainer.id:
            form.errors["template_id"] = "Wybierz plan ze swojej biblioteki."
    return form


def library(session, trainer: Trainer) -> list[PlanTemplate]:
    return list(
        session.scalars(
            select(PlanTemplate)
            .where(PlanTemplate.trainer_id == trainer.id)
            .order_by(PlanTemplate.name)
        )
    )


def form_page(request, session, trainer, form: NewTrainee, status=200):
    response = render(
        request,
        "trainee_new.html",
        session,
        trainer,
        section="add_trainee",
        form=form,
        library=library(session, trainer),
        today=today(),
    )
    response.status_code = status
    return response


@router.get("/trainees/new")
def new_trainee(request: Request, session: SessionDep, trainer: TrainerDep):
    return form_page(request, session, trainer, NewTrainee(consent_date=today().isoformat()))


@router.post("/trainees/new")
def create_trainee(
    request: Request,
    session: SessionDep,
    trainer: TrainerDep,
    name: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    consent_date: Annotated[str, Form()] = "",
    template_id: Annotated[str, Form()] = "",
):
    form = check(session, trainer, NewTrainee(name, phone, consent_date, template_id.strip()))
    if form.errors:
        return form_page(request, session, trainer, form, status=422)
    trainee = Trainee(
        trainer_id=trainer.id,
        name=form.name,
        phone=form.phone_digits,
        consent_date=form.consent,
        timezone=DEFAULT_TIMEZONE,
    )
    session.add(trainee)
    session.flush()
    if form.template is not None:
        plan = TrainingPlan.model_validate(form.template.plan)
        plan_history.assign(session, trainee, plan, "template", form.template)
    session.commit()
    log.info("Trainer %s added trainee %s (%s)", trainer.id, trainee.id, trainee.name)
    return RedirectResponse(f"/dashboard?added={trainee.id}", 303)


@lru_cache
def _bot_username(token: str) -> str:
    return telegram.bot_username(Settings(telegram_bot_token=token))


def bot_link(settings: Settings) -> str | None:
    """t.me/<bot>, the link a new trainee opens; None if Telegram can't be asked (logged)."""
    if settings.channel != "telegram" or not settings.telegram_bot_token:
        return None
    try:
        return f"https://t.me/{_bot_username(settings.telegram_bot_token)}"
    except Exception as e:  # TelegramError, or no network
        log.warning("Can't get the bot's username for the dashboard: %s", e)
        return None


def added_notice(session, settings: Settings, trainer: Trainer, trainee_id: str | None):
    """For the overview after adding a trainee: (the trainee, the bot's link) or None."""
    if not trainee_id or not trainee_id.isdigit():
        return None
    trainee = session.get(Trainee, int(trainee_id))
    if trainee is None or trainee.trainer_id != trainer.id:
        return None
    return trainee, bot_link(settings)
