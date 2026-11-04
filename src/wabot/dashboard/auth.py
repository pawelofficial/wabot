"""Who is signed in, and which trainees they may see.

The session is a signed cookie holding the trainer's ID. Every dashboard page depends on
`current_trainer` (no valid cookie: redirect to the login page), and every trainee page on
`trainee_of_trainer` (a trainee of another trainer: 404, as if it didn't exist; the same for library
plans, `template_of_trainer`, and plan versions, `version_of_trainee`). Pages take the
trainer from these, never from the URL or a form, so one trainer can't reach another's data.

Signing in: with Google (GOOGLE_CLIENT_ID): Google's button on the login page gives the browser
an ID token, a JWT signed by Google saying which account signed in; the page posts it to
/dashboard/login/google, verify_google_token() checks it, and the trainer is the one with that
email (trainers.email). With DASHBOARD_DEV_LOGIN=True (local only) the login page also lists the
trainers to sign in as, without Google.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from wabot.config import Settings, get_settings
from wabot.db import get_session
from wabot.models import PlanTemplate, Trainee, TraineePlan, Trainer

COOKIE = "wabot_session"
MAX_AGE = 30 * 24 * 3600  # signed in for 30 days
# Only used with the dev login, which never runs in production.
DEV_SECRET = "dev-login-only-not-secret"

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


def _serializer(settings: Settings) -> URLSafeTimedSerializer | None:
    secret = settings.dashboard_session_secret or (
        DEV_SECRET if settings.dashboard_dev_login else ""
    )
    return URLSafeTimedSerializer(secret, salt="dashboard-session") if secret else None


def sign_in(response: Response, request: Request, settings: Settings, trainer: Trainer) -> None:
    serializer = _serializer(settings)
    if serializer is None:
        raise HTTPException(500, "DASHBOARD_SESSION_SECRET is not set")
    response.set_cookie(
        COOKIE,
        serializer.dumps(trainer.id),
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def verify_google_token(token: str, client_id: str) -> str | None:
    """The verified email of the Google account in the ID token, or None if the token isn't
    valid: not signed by Google, expired, issued for another app, or the email isn't verified."""
    try:
        # Fetches Google's public keys (cached by HTTP headers) and checks signature, expiry,
        # issuer and audience.
        claims = id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
    except ValueError:
        return None
    if not claims.get("email_verified") or not claims.get("email"):
        return None
    return claims["email"]


def sign_out(response: Response) -> None:
    response.delete_cookie(COOKIE)


class NotSignedIn(Exception):
    """Raised by current_trainer; main.py turns it into a redirect to the login page."""


def current_trainer(request: Request, settings: SettingsDep, session: SessionDep) -> Trainer:
    cookie = request.cookies.get(COOKIE)
    serializer = _serializer(settings)
    if not cookie or serializer is None:
        raise NotSignedIn
    try:
        trainer_id = serializer.loads(cookie, max_age=MAX_AGE)
    except BadSignature:  # also an expired cookie
        raise NotSignedIn from None
    trainer = session.get(Trainer, trainer_id)
    if trainer is None:
        raise NotSignedIn
    return trainer


TrainerDep = Annotated[Trainer, Depends(current_trainer)]


def trainee_of_trainer(trainee_id: int, trainer: TrainerDep, session: SessionDep) -> Trainee:
    """The trainee in the URL, if they're the signed-in trainer's; 404 otherwise."""
    trainee = session.get(Trainee, trainee_id)
    if trainee is None or trainee.trainer_id != trainer.id:
        raise HTTPException(404)
    return trainee


TraineeDep = Annotated[Trainee, Depends(trainee_of_trainer)]


def template_of_trainer(template_id: int, trainer: TrainerDep, session: SessionDep) -> PlanTemplate:
    """The library plan in the URL, if it's the signed-in trainer's; 404 otherwise."""
    template = session.get(PlanTemplate, template_id)
    if template is None or template.trainer_id != trainer.id:
        raise HTTPException(404)
    return template


TemplateDep = Annotated[PlanTemplate, Depends(template_of_trainer)]


def version_of_trainee(version_id: int, trainee: TraineeDep, session: SessionDep) -> TraineePlan:
    """A version of the trainee's plan (the history), if it's that trainee's; 404 otherwise."""
    version = session.get(TraineePlan, version_id)
    if (
        version is None
        or version.trainee_id != trainee.id
        or version.trainer_id != trainee.trainer_id
    ):
        raise HTTPException(404)
    return version


VersionDep = Annotated[TraineePlan, Depends(version_of_trainee)]
