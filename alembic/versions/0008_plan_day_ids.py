"""Every plan day gets an ID that stays the same when the day is renamed, and each training
check-in points to its day by it (training_checkins.plan_day_id).

Stored plans had no day IDs, so here a day's ID is passed on by name, the way the app does it
for a plan without IDs (plans.with_day_ids()): library plans get new IDs; each trainee's
versions are gone through oldest first, and a day takes the ID of the day with the same name in
the library plan it came from (for a version assigned from the library) or in the trainee's
earlier versions, newest first; otherwise a new one. A rename made before this migration can't
be told from a new day, so the renamed day gets a new ID. Check-ins get the ID of the day with
their plan_day name in their plan version.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-04
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

templates = sa.table("plan_templates", sa.column("id", sa.Integer), sa.column("plan", sa.JSON))
versions = sa.table(
    "trainee_plans",
    sa.column("id", sa.Integer),
    sa.column("trainee_id", sa.Integer),
    sa.column("plan", sa.JSON),
    sa.column("source", sa.String),
    sa.column("template_id", sa.Integer),
)
checkins = sa.table(
    "training_checkins",
    sa.column("id", sa.Integer),
    sa.column("plan_version_id", sa.Integer),
    sa.column("plan_day", sa.Text),
    sa.column("plan_day_id", sa.String),
)


def _key(name: str) -> str:
    # As plans._key(): days are the same day whatever the case and spacing.
    return " ".join(str(name).casefold().split())


def _days(plan) -> list[dict]:
    return plan.get("days", []) if isinstance(plan, dict) else []


def _with_day_ids(plan: dict, earlier: list[dict], seen: set[str]) -> dict:
    """As plans.with_day_ids(), on the stored JSON."""
    taken = {d["id"] for d in _days(plan) if d.get("id")}
    days = []
    for day in _days(plan):
        if not day.get("id"):
            same_name = (
                d["id"]
                for p in earlier
                for d in _days(p)
                if d.get("id") and d["id"] not in taken and _key(d["name"]) == _key(day["name"])
            )
            day_id = next(same_name, None)
            if day_id is None:
                # A new ID, never one another day has had.
                day_id = secrets.token_hex(4)
                while day_id in seen:
                    day_id = secrets.token_hex(4)
            day = {"id": day_id, **day}
        taken.add(day["id"])
        seen.add(day["id"])
        days.append(day)
    return {**plan, "days": days}


def upgrade() -> None:
    with op.batch_alter_table("training_checkins") as batch_op:
        batch_op.add_column(sa.Column("plan_day_id", sa.String(length=32), nullable=True))

    bind = op.get_bind()
    seen: set[str] = set()
    library = {}
    for template_id, plan in bind.execute(sa.select(templates.c.id, templates.c.plan)).all():
        library[template_id] = _with_day_ids(plan, [], seen)
        bind.execute(
            templates.update()
            .where(templates.c.id == template_id)
            .values(plan=library[template_id])
        )

    rows = bind.execute(
        sa.select(
            versions.c.id,
            versions.c.trainee_id,
            versions.c.plan,
            versions.c.source,
            versions.c.template_id,
        ).order_by(versions.c.trainee_id, versions.c.id)
    ).all()
    earlier_of: dict[int, list[dict]] = {}  # per trainee, newest first
    with_ids = {}
    for version_id, trainee_id, plan, source, template_id in rows:
        earlier = earlier_of.setdefault(trainee_id, [])
        template = [library[template_id]] if template_id in library else []
        # A copy of a library plan takes its days' IDs first; other versions their trainee's.
        sources = template + earlier if source == "template" else earlier + template
        with_ids[version_id] = _with_day_ids(plan, sources, seen)
        earlier.insert(0, with_ids[version_id])
        bind.execute(
            versions.update().where(versions.c.id == version_id).values(plan=with_ids[version_id])
        )

    rows = bind.execute(
        sa.select(checkins.c.id, checkins.c.plan_version_id, checkins.c.plan_day).where(
            checkins.c.plan_version_id.is_not(None), checkins.c.plan_day.is_not(None)
        )
    ).all()
    for checkin_id, version_id, plan_day in rows:
        day_id = next(
            (d["id"] for d in _days(with_ids.get(version_id)) if _key(d["name"]) == _key(plan_day)),
            None,
        )
        if day_id is not None:
            bind.execute(
                checkins.update().where(checkins.c.id == checkin_id).values(plan_day_id=day_id)
            )


def downgrade() -> None:
    # Without day IDs again: the code before this migration refuses a day with an "id".
    bind = op.get_bind()
    for table in (templates, versions):
        for row_id, plan in bind.execute(sa.select(table.c.id, table.c.plan)).all():
            if isinstance(plan, dict):
                days = [{k: v for k, v in d.items() if k != "id"} for d in _days(plan)]
                bind.execute(
                    table.update().where(table.c.id == row_id).values(plan={**plan, "days": days})
                )
    with op.batch_alter_table("training_checkins") as batch_op:
        batch_op.drop_column("plan_day_id")
