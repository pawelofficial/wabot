"""training_checkins.outside_plan: a day's training of the plan and its training outside the
plan (a run) are both current, each the newest report of its kind (entries.outside_plan()).

Existing check-ins get it by the same rule: outside the plan when there's no plan day, it
isn't a skipped one, and none of its exercises is in the plan or replaces a plan exercise.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

checkins = sa.table(
    "training_checkins",
    sa.column("id", sa.Integer),
    sa.column("plan_day", sa.Text),
    sa.column("status", sa.String),
    sa.column("outside_plan", sa.Boolean),
)
exercises = sa.table(
    "training_exercises",
    sa.column("checkin_id", sa.Integer),
    sa.column("in_plan", sa.Boolean),
    sa.column("replaces", sa.String),
)


def upgrade() -> None:
    with op.batch_alter_table("training_checkins") as batch_op:
        batch_op.add_column(
            sa.Column("outside_plan", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    from_plan = (
        sa.select(exercises.c.checkin_id)
        .where(
            exercises.c.checkin_id == checkins.c.id,
            sa.or_(exercises.c.in_plan.is_(True), exercises.c.replaces.is_not(None)),
        )
        .exists()
    )
    op.execute(
        checkins.update()
        .where(checkins.c.plan_day.is_(None), checkins.c.status != "skipped", ~from_plan)
        .values(outside_plan=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("training_checkins") as batch_op:
        batch_op.drop_column("outside_plan")
