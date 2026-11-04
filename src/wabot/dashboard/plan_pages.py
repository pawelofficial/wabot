"""The training plan pages: a trainee's plan (its history, assigning a plan from the library,
the editor) and the trainer's plan library (Biblioteka planów).

A trainee's plan changes only through plan_history.assign(), which keeps every version. A
library plan is the trainer's own; assigning it copies it to the trainee (plan_history).
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from wabot import exercises, plan_history, plans
from wabot.dashboard import plan_editor
from wabot.dashboard.auth import SessionDep, TemplateDep, TraineeDep, TrainerDep, VersionDep
from wabot.dashboard.routes import render
from wabot.models import PlanTemplate, Trainee, TraineePlan, Trainer
from wabot.plans import TrainingPlan

router = APIRouter(prefix="/dashboard", default_response_class=HTMLResponse)

# What the plan page says after an action (?done=...).
DONE = {
    "saved": "Zapisano plan. Yanek korzysta z niego od następnej wiadomości podopiecznego.",
    "unchanged": "Plan bez zmian, nic nie zapisano.",
    "assigned": "Przypisano plan z biblioteki. Yanek korzysta z niego od następnej wiadomości "
    "podopiecznego.",
    "restored": "Przywrócono wcześniejszy plan. Yanek korzysta z niego od następnej wiadomości "
    "podopiecznego.",
    "already_assigned": "Podopieczny ma już ten plan z biblioteki, bez zmian. Nic nie zapisano.",
    "to_library": "Zapisano plan w bibliotece.",
    "deleted": "Usunięto plan z biblioteki. Podopieczni, którym go przypisano, mają go nadal.",
}


def trainee_page(trainee: Trainee, done: str | None = None) -> RedirectResponse:
    query = f"?done={done}" if done else ""
    return RedirectResponse(f"/dashboard/trainees/{trainee.id}/plan{query}", 303)


def users_of(session, trainer: Trainer) -> dict[int, list[tuple[Trainee, bool]]]:
    """Per library plan: the trainer's trainees whose current plan came from it, and whether it
    was edited since. Only trainees who are the trainer's now (one moved to another trainer
    keeps their old versions, which mustn't show them here)."""
    newest = (
        select(func.max(TraineePlan.id))
        .join(Trainee, Trainee.id == TraineePlan.trainee_id)
        .where(TraineePlan.trainer_id == trainer.id, Trainee.trainer_id == trainer.id)
        .group_by(TraineePlan.trainee_id)
    )
    rows = session.execute(
        select(TraineePlan, Trainee)
        .join(Trainee, Trainee.id == TraineePlan.trainee_id)
        .where(TraineePlan.id.in_(newest), Trainee.training_plan.is_not(None))
        .order_by(Trainee.name)
    )
    users: dict[int, list] = {}
    for version, trainee in rows:
        if version.template_id is not None:
            users.setdefault(version.template_id, []).append((trainee, version.source == "edited"))
    return users


def name_error(session, trainer: Trainer, name: str, template: PlanTemplate | None) -> str | None:
    if not name:
        return "Podaj nazwę planu."
    if len(name) > 100:
        return "Nazwa planu może mieć najwyżej 100 znaków."
    other = session.scalars(
        select(PlanTemplate).where(
            PlanTemplate.trainer_id == trainer.id,
            func.lower(PlanTemplate.name) == name.lower(),
        )
    ).first()
    if other is not None and other is not template:
        return f"W bibliotece jest już plan „{other.name}”. Wybierz inną nazwę."
    return None


# A trainee's plan


def trainee_plan_page(request, session, trainer, trainee, done=None, status=200, **extra):
    history = plan_history.history(session, trainee)
    has_plan = trainee.training_plan is not None
    response = render(
        request,
        "plan.html",
        session,
        trainer,
        trainee,
        "plan",
        plan=plans.of(trainee),
        current=history[0] if history and has_plan else None,
        history=history,
        library=plan_history.library(session, trainer.id),
        sources=plan_history.SOURCES,
        catalog=exercises.BY_ID,
        done=DONE.get(done),
        **extra,
    )
    response.status_code = status
    return response


@router.get("/trainees/{trainee_id}/plan")
def trainee_plan(
    request: Request,
    session: SessionDep,
    trainer: TrainerDep,
    trainee: TraineeDep,
    done: str | None = None,
):
    """The trainee's current plan, a library plan to assign instead, and every earlier plan."""
    return trainee_plan_page(request, session, trainer, trainee, done)


def editor_page(
    request,
    session,
    trainer,
    trainee,
    data,
    *,
    heading,
    cancel_url,
    errors=(),
    status=200,
    name=None,
    library=False,
):
    """The plan editor (plan_edit.html) for a trainee's plan or a library plan (with a name)."""
    response = render(
        request,
        "plan_edit.html",
        session,
        trainer,
        trainee,
        "plan" if trainee else "library",
        data=data,
        errors=errors,
        groups=plan_editor.exercise_groups(),
        catalog=exercises.BY_ID,
        heading=heading,
        cancel_url=cancel_url,
        name=name,
        library=library,
    )
    response.status_code = status
    return response


def trainee_editor(request, session, trainer, trainee, data, errors=(), status=200):
    heading = "Edycja planu" if trainee.training_plan else "Nowy plan"
    return editor_page(
        request,
        session,
        trainer,
        trainee,
        data,
        heading=f"{heading} – {trainee.name}",
        cancel_url=f"/dashboard/trainees/{trainee.id}/plan",
        errors=errors,
        status=status,
    )


@router.get("/trainees/{trainee_id}/plan/edit")
def edit_trainee_plan(
    request: Request, session: SessionDep, trainer: TrainerDep, trainee: TraineeDep
):
    data = plan_editor.form_data(plans.of(trainee))
    return trainee_editor(request, session, trainer, trainee, data)


@router.post("/trainees/{trainee_id}/plan/edit")
def save_trainee_plan(
    request: Request,
    session: SessionDep,
    trainer: TrainerDep,
    trainee: TraineeDep,
    plan: Annotated[str, Form()],
):
    """Save the edited plan (the editor's JSON) as a new version. The bot uses it from the
    trainee's next message on; saved check-ins keep the plan day they were saved with."""
    data, new_plan, errors = plan_editor.parse(plan)
    if errors:
        return trainee_editor(request, session, trainer, trainee, data, errors, 422)
    version = plan_history.assign(session, trainee, new_plan, "edited")
    session.commit()
    return trainee_page(trainee, "saved" if version else "unchanged")


@router.post("/trainees/{trainee_id}/plan/assign")
def assign_library_plan(
    session: SessionDep,
    trainer: TrainerDep,
    trainee: TraineeDep,
    template_id: Annotated[int, Form()],
):
    """A copy of a library plan becomes the trainee's plan."""
    template = session.get(PlanTemplate, template_id)
    if template is None or template.trainer_id != trainer.id:
        raise HTTPException(404)
    plan = TrainingPlan.model_validate(template.plan)
    version = plan_history.assign(session, trainee, plan, "template", template)
    session.commit()
    return trainee_page(trainee, "assigned" if version else "already_assigned")


@router.post("/trainees/{trainee_id}/plan/to-library")
def copy_to_library(
    request: Request,
    session: SessionDep,
    trainer: TrainerDep,
    trainee: TraineeDep,
    name: Annotated[str, Form()] = "",
):
    """The trainee's current plan, copied into the library under a name."""
    plan = plans.of(trainee)
    if plan is None:
        raise HTTPException(404)
    name = " ".join(name.split())
    if error := name_error(session, trainer, name, None):
        return trainee_plan_page(
            request, session, trainer, trainee, status=422, library_error=error, library_name=name
        )
    session.add(PlanTemplate(trainer_id=trainer.id, name=name, plan=plans.to_db(plan)))
    session.commit()
    return trainee_page(trainee, "to_library")


@router.get("/trainees/{trainee_id}/plan/versions/{version_id}")
def plan_version(
    request: Request,
    session: SessionDep,
    trainer: TrainerDep,
    trainee: TraineeDep,
    version: VersionDep,
):
    """An earlier (or the current) version of the trainee's plan."""
    current = plan_history.current_version(session, trainee)
    return render(
        request,
        "plan_version.html",
        session,
        trainer,
        trainee,
        "plan",
        version=version,
        plan=TrainingPlan.model_validate(version.plan),
        is_current=current is not None
        and current.id == version.id
        and trainee.training_plan is not None,
        sources=plan_history.SOURCES,
        catalog=exercises.BY_ID,
    )


@router.post("/trainees/{trainee_id}/plan/versions/{version_id}/restore")
def restore_version(
    session: SessionDep, trainer: TrainerDep, trainee: TraineeDep, version: VersionDep
):
    restored = plan_history.restore(session, trainee, version)
    session.commit()
    return trainee_page(trainee, "restored" if restored else "unchanged")


# The plan library


@router.get("/plans")
def library(request: Request, session: SessionDep, trainer: TrainerDep, done: str | None = None):
    """The trainer's library plans, with the trainees who have each."""
    return render(
        request,
        "library.html",
        session,
        trainer,
        section="library",
        templates=plan_history.library(session, trainer.id),
        users=users_of(session, trainer),
        done=DONE.get(done),
    )


def library_editor(request, session, trainer, template, data, name, errors=(), status=200):
    return editor_page(
        request,
        session,
        trainer,
        None,
        data,
        heading=f"Edycja planu – {template.name}" if template else "Nowy plan w bibliotece",
        cancel_url=f"/dashboard/plans/{template.id}" if template else "/dashboard/plans",
        errors=errors,
        status=status,
        name=name,
        library=True,
    )


def save_library_plan(request, session, trainer, template, plan_json, name):
    name = " ".join(name.split())
    data, new_plan, errors = plan_editor.parse(plan_json)
    if error := name_error(session, trainer, name, template):
        errors = [error, *errors]
    if errors:
        return library_editor(request, session, trainer, template, data, name, errors, 422)
    if template is None:
        template = PlanTemplate(trainer_id=trainer.id)
        session.add(template)
    template.name = name
    template.plan = plans.to_db(new_plan)
    session.commit()
    return RedirectResponse(f"/dashboard/plans/{template.id}", 303)


@router.get("/plans/new")
def new_library_plan(request: Request, session: SessionDep, trainer: TrainerDep):
    return library_editor(request, session, trainer, None, plan_editor.form_data(None), "")


@router.post("/plans/new")
def create_library_plan(
    request: Request,
    session: SessionDep,
    trainer: TrainerDep,
    plan: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
):
    return save_library_plan(request, session, trainer, None, plan, name)


@router.get("/plans/{template_id}")
def library_plan(request: Request, session: SessionDep, trainer: TrainerDep, template: TemplateDep):
    return render(
        request,
        "library_plan.html",
        session,
        trainer,
        section="library",
        library_plan=template,
        plan=TrainingPlan.model_validate(template.plan),
        users=users_of(session, trainer).get(template.id, []),
        catalog=exercises.BY_ID,
    )


@router.get("/plans/{template_id}/edit")
def edit_library_plan(
    request: Request, session: SessionDep, trainer: TrainerDep, template: TemplateDep
):
    data = plan_editor.form_data(TrainingPlan.model_validate(template.plan))
    return library_editor(request, session, trainer, template, data, template.name)


@router.post("/plans/{template_id}/edit")
def update_library_plan(
    request: Request,
    session: SessionDep,
    trainer: TrainerDep,
    template: TemplateDep,
    plan: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
):
    """Changes the library plan only; trainees who got it keep their copy."""
    return save_library_plan(request, session, trainer, template, plan, name)


@router.post("/plans/{template_id}/delete")
def delete_library_plan(session: SessionDep, trainer: TrainerDep, template: TemplateDep):
    """Trainees keep the plan they got; their history still names it (TraineePlan.name)."""
    session.delete(template)
    session.commit()
    return RedirectResponse("/dashboard/plans?done=deleted", 303)
