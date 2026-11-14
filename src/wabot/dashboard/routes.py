"""The dashboard's pages. Each one renders a template that extends base.html (the sidebar with
the trainee dropdown). install() adds them to the FastAPI app in main.py."""

import datetime as dt
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot import trainees
from wabot.dashboard import auth, queries
from wabot.dashboard.auth import SessionDep, SettingsDep, TraineeDep, TrainerDep
from wabot.models import DEFAULT_TIMEZONE, Trainee, Trainer

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")

MONTHS = "sty lut mar kwi maj cze lip sie wrz paź lis gru".split()
MONTH_NAMES = (
    "styczeń luty marzec kwiecień maj czerwiec lipiec sierpień wrzesień październik listopad "
    "grudzień"
).split()
# "w październiku"
MONTHS_IN = (
    "styczniu lutym marcu kwietniu maju czerwcu lipcu sierpniu wrześniu październiku listopadzie "
    "grudniu"
).split()
WEEKDAYS = "pon wt śr czw pt sob niedz".split()


def pl_date(day: dt.date, weekday: bool = False) -> str:
    """5 paź 2026, or with weekday=True: pon 5 paź 2026."""
    text = f"{day.day} {MONTHS[day.month - 1]} {day.year}"
    return f"{WEEKDAYS[day.weekday()]} {text}" if weekday else text


def pl_month(month: dt.date, in_: bool = False) -> str:
    """październik 2026, or with in_=True: w październiku 2026."""
    if in_:
        return f"w {MONTHS_IN[month.month - 1]} {month.year}"
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def pl_datetime(moment: dt.datetime, timezone: str) -> str:
    """5 paź 2026, 18:30 in the given timezone."""
    local = moment.astimezone(ZoneInfo(timezone))
    return f"{pl_date(local.date())}, {local:%H:%M}"


def pl_count(n: int, one: str, few: str, many: str) -> str:
    """1 ćwiczenie, 3 ćwiczenia, 5 ćwiczeń (Polish plural forms)."""
    if n == 1:
        word = one
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = few
    else:
        word = many
    return f"{n} {word}"


def pl_number(x: float) -> str:
    """62,5 and 60 (no ",0")."""
    return f"{x:g}".replace(".", ",")


def pl_dose(exercise) -> str:
    """What was done of a reported exercise (models.TrainingExercise): 4 × 8, 62,5 kg; 3 serie;
    28 min."""
    sets, reps, kg = exercise.sets, exercise.reps, exercise.weight_kg
    if sets and reps:
        done = f"{sets} × {reps}"
    elif sets:
        done = pl_count(sets, "seria", "serie", "serii")
    else:
        done = reps
    return ", ".join(part for part in (done, kg and f"{pl_number(kg)} kg") if part)


def pl_kg(kg: float, signed: bool = False) -> str:
    """82,4 kg; signed=True: +0,4 kg / −1,2 kg (a real minus sign)."""
    text = f"{abs(kg) if signed else kg:.1f}".replace(".", ",") + " kg"
    if signed:
        text = ("+" if kg > 0 else "−" if kg < 0 else "±") + text
    return text


templates.env.filters["pl_date"] = pl_date
templates.env.filters["pl_kg"] = pl_kg
templates.env.filters["pl_month"] = pl_month
templates.env.filters["pl_number"] = pl_number
templates.env.filters["pl_dose"] = pl_dose
templates.env.filters["pl_datetime"] = pl_datetime
templates.env.filters["pl_count"] = pl_count
# Trainers have no timezone of their own yet; their times are shown in this one.
templates.env.globals["default_timezone"] = DEFAULT_TIMEZONE
router = APIRouter(prefix="/dashboard", default_response_class=HTMLResponse)

# The trainee pages in the sidebar: (URL part, label, icon).
TRAINEE_SECTIONS = [
    ("weight", "Waga", "⚖️"),
    ("trainings", "Treningi", "🏋️"),
    ("plan", "Plan treningowy", "📋"),
]


def install(app: FastAPI) -> None:
    # They use render() from here.
    from wabot.dashboard import plan_pages, trainee_pages, trainer_pages, training_pages

    # Before router: /trainees/new isn't a trainee's ID.
    app.include_router(trainee_pages.router)
    app.include_router(trainer_pages.router)
    app.include_router(router)
    app.include_router(plan_pages.router)
    app.include_router(training_pages.router)
    app.mount("/dashboard/static", StaticFiles(directory=HERE / "static"), name="static")
    # A page that needs a signed-in trainer, without one: go to the login page.
    app.add_exception_handler(
        auth.NotSignedIn, lambda request, exc: RedirectResponse("/dashboard/login", 303)
    )


def render(
    request: Request,
    template: str,
    session: Session,
    trainer: Trainer,
    trainee: Trainee | None = None,
    section: str | None = None,
    **context,
) -> HTMLResponse:
    """A page with the sidebar: the trainer's trainees in the dropdown, `trainee` chosen."""
    own_trainees = session.scalars(
        select(Trainee).where(Trainee.trainer_id == trainer.id).order_by(Trainee.name)
    ).all()
    return templates.TemplateResponse(
        request,
        template,
        {
            "trainer": trainer,
            "trainees": own_trainees,
            "trainee": trainee,
            "section": section,
            "is_admin": getattr(request.state, "is_admin", False),
            "sections": TRAINEE_SECTIONS,
            # Choosing another trainee opens the same page for them (Treningi from the overview).
            "dropdown_section": section
            if section in {s[0] for s in TRAINEE_SECTIONS}
            else "trainings",
            **context,
        },
    )


@router.get("")
def overview(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    trainer: TrainerDep,
    added: str | None = None,
):
    """Home: the trainer's trainees; after adding one (?added=<id>), how they connect."""
    from wabot.dashboard.trainee_pages import added_notice

    return render(
        request,
        "overview.html",
        session,
        trainer,
        section="overview",
        added=added_notice(session, settings, trainer, added),
    )


@router.get("/trainees/{trainee_id}/weight")
def weight(request: Request, session: SessionDep, trainer: TrainerDep, trainee: TraineeDep):
    """The weight chart: the current weight of each day (the newest report of the day)."""
    weigh_ins = queries.weigh_ins(session, trainee)
    return render(
        request,
        "weight.html",
        session,
        trainer,
        trainee,
        "weight",
        weigh_ins=weigh_ins,
        chart_data=[w.as_json() for w in weigh_ins],
    )


def login_page_response(
    request: Request, settings, session: Session, error: str | None = None, status: int = 200
) -> HTMLResponse:
    trainers = (
        session.scalars(select(Trainer).order_by(Trainer.name)).all()
        if settings.dashboard_dev_login
        else []
    )
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "dev_login": settings.dashboard_dev_login,
            "trainers": trainers,
            "google_client_id": settings.google_client_id,
            "google_login_uri": str(request.url_for("google_login")),
            "missing_secret": not settings.dashboard_session_secret,
            "error": error,
        },
        status_code=status,
    )


@router.get("/login")
def login_page(request: Request, settings: SettingsDep, session: SessionDep):
    return login_page_response(request, settings, session)


@router.post("/login/google", name="google_login")
def google_login(
    request: Request,
    settings: SettingsDep,
    session: SessionDep,
    credential: Annotated[str, Form()],
    g_csrf_token: Annotated[str, Form()],
):
    """Google's sign-in button posts the ID token here."""
    if not settings.google_client_id:
        raise HTTPException(404)
    # Google sets the same random value as a cookie and a form field; another site posting
    # here can't set the cookie.
    if request.cookies.get("g_csrf_token") != g_csrf_token:
        raise HTTPException(400, "CSRF token mismatch")
    email = auth.verify_google_token(credential, settings.google_client_id)
    if email is None:
        return login_page_response(
            request, settings, session, "Nie udało się zalogować przez Google.", 401
        )
    trainer = trainees.trainer_by_email(session, email)
    if trainer is None:
        return login_page_response(
            request, settings, session, f"Brak dostępu: {email} nie jest kontem trenera.", 403
        )
    response = RedirectResponse("/dashboard", 303)
    auth.sign_in(response, request, settings, trainer)
    return response


@router.post("/login/dev")
def dev_login(
    request: Request,
    settings: SettingsDep,
    session: SessionDep,
    trainer_id: Annotated[int, Form()],
):
    """Sign in as any trainer, without a password. Only with DASHBOARD_DEV_LOGIN=True."""
    if not settings.dashboard_dev_login:
        raise HTTPException(404)
    trainer = session.get(Trainer, trainer_id)
    if trainer is None:
        raise HTTPException(404)
    response = RedirectResponse("/dashboard", 303)
    auth.sign_in(response, request, settings, trainer)
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/dashboard/login", 303)
    auth.sign_out(response)
    return response
