"""A trainee's training plans over time, and the trainer's plan library.

Every change to a trainee's plan goes through assign(): it adds a TraineePlan row (the version)
and sets trainees.training_plan, the copy the bot reads (plans.of()). So the newest version is
always the current plan, and earlier ones can be looked at and restored.

A library plan (PlanTemplate) belongs to the trainer, not a trainee. Assigning it copies the
plan into a new version: editing or deleting the library plan later doesn't change the trainees
who got it, and their trainings stay saved against the plan they really had.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from wabot import plans
from wabot.models import PlanTemplate, Trainee, TraineePlan
from wabot.plans import TrainingPlan

# TraineePlan.source, and how the dashboard names it.
SOURCES = {
    "edited": "edycja",
    "template": "z biblioteki",
    "restored": "przywrócony",
    "script": "skrypt add_trainee",
    "imported": "sprzed historii planów",
}


def assign(
    session: Session,
    trainee: Trainee,
    plan: TrainingPlan,
    source: str,
    template: PlanTemplate | None = None,
    name: str | None = None,
) -> TraineePlan | None:
    """Make `plan` the trainee's current plan, as a new version. An edit that changes nothing
    adds no version (returns None). The caller commits."""
    if source not in SOURCES:
        raise ValueError(f"unknown plan source {source!r}")
    # A trainee being registered gets its id, a changed trainer its trainer_id.
    session.add(trainee)
    session.flush()
    data = plans.to_db(plan)
    current = current_version(session, trainee)
    if source == "edited" and current is not None and current.plan == data:
        return None
    template_id = template.id if template else None
    if template is not None:
        name = template.name
    elif source == "edited" and current is not None:
        # An edited plan is still based on the same library plan.
        template_id, name = current.template_id, current.name
    version = TraineePlan(
        trainer_id=trainee.trainer_id,
        trainee_id=trainee.id,
        plan=data,
        source=source,
        template_id=template_id,
        name=name,
    )
    trainee.training_plan = data
    session.add(version)
    session.flush()
    return version


def restore(session: Session, trainee: Trainee, version: TraineePlan) -> TraineePlan:
    """An earlier version becomes the current plan again (as a new version; nothing is
    removed)."""
    new = assign(session, trainee, TrainingPlan.model_validate(version.plan), "restored")
    new.template_id, new.name = version.template_id, version.name
    return new


def history(session: Session, trainee: Trainee) -> list[TraineePlan]:
    """The trainee's plan versions, newest (the current one) first."""
    return list(
        session.scalars(
            select(TraineePlan)
            .where(
                TraineePlan.trainee_id == trainee.id,
                TraineePlan.trainer_id == trainee.trainer_id,
            )
            .order_by(TraineePlan.id.desc())
        )
    )


def current_version(session: Session, trainee: Trainee) -> TraineePlan | None:
    return session.scalars(
        select(TraineePlan)
        .where(TraineePlan.trainee_id == trainee.id)
        .order_by(TraineePlan.id.desc())
        .limit(1)
    ).first()


def library(session: Session, trainer_id: int) -> list[PlanTemplate]:
    return list(
        session.scalars(
            select(PlanTemplate)
            .where(PlanTemplate.trainer_id == trainer_id)
            .order_by(PlanTemplate.name)
        )
    )
