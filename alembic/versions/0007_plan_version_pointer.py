"""A trainee points to their current plan version instead of holding a copy of it, and each
training check-in points to the plan version it was saved against.

- trainees.training_plan (the current plan, copied from its version) becomes
  trainees.plan_version_id, a foreign key to the trainee's own version. A trainee whose copy
  isn't the same as their newest version (or who has no version) gets it as a new "imported"
  version first, so the bot keeps reading exactly the plan it read before.
- training_checkins.plan_version_id: the version that was current when the check-in was last
  saved (its updated_at), the trainee's own. Null when the trainee had no version yet.
- trainee_plans gets a unique (trainee_id, id), which both foreign keys point to ("a version of
  this trainee"); it replaces the plain index on the same columns.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-04
"""

import datetime as dt
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

trainees = sa.table(
    "trainees",
    sa.column("id", sa.Integer),
    sa.column("trainer_id", sa.Integer),
    sa.column("training_plan", sa.JSON),
    sa.column("plan_version_id", sa.Integer),
)
versions = sa.table(
    "trainee_plans",
    sa.column("id", sa.Integer),
    sa.column("trainer_id", sa.Integer),
    sa.column("trainee_id", sa.Integer),
    sa.column("plan", sa.JSON),
    sa.column("source", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def _now(bind) -> dt.datetime:
    now = dt.datetime.now(dt.UTC)
    # The app stores naive UTC on SQLite (models.UTCDateTime).
    return now.replace(tzinfo=None) if bind.dialect.name == "sqlite" else now


def _point_trainees_to_their_versions(bind) -> None:
    # A version with a JSON null plan isn't a plan (migration 0006 copied trainees.training_plan
    # as it was, and None can be stored as JSON null).
    for version_id, plan in bind.execute(sa.select(versions.c.id, versions.c.plan)).all():
        if plan is None:
            bind.execute(versions.delete().where(versions.c.id == version_id))

    rows = bind.execute(
        sa.select(trainees.c.id, trainees.c.trainer_id, trainees.c.training_plan)
    ).all()
    for trainee_id, trainer_id, plan in rows:
        if not plan:  # SQL NULL or JSON null: no plan, the pointer stays null
            continue
        newest = bind.execute(
            sa.select(versions.c.id, versions.c.plan)
            .where(versions.c.trainee_id == trainee_id)
            .order_by(versions.c.id.desc())
            .limit(1)
        ).first()
        if newest is None or newest.plan != plan:
            bind.execute(
                versions.insert().values(
                    trainer_id=trainer_id,
                    trainee_id=trainee_id,
                    plan=plan,
                    source="imported",
                    created_at=_now(bind),
                )
            )
            newest = bind.execute(
                sa.select(sa.func.max(versions.c.id)).where(versions.c.trainee_id == trainee_id)
            ).first()
        bind.execute(
            trainees.update().where(trainees.c.id == trainee_id).values(plan_version_id=newest[0])
        )


def upgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("trainee_plans") as batch_op:
        batch_op.drop_index("ix_trainee_plans_trainee_id_id")
        batch_op.create_unique_constraint("uq_trainee_plans_trainee_id_id", ["trainee_id", "id"])

    with op.batch_alter_table("trainees") as batch_op:
        batch_op.add_column(sa.Column("plan_version_id", sa.Integer(), nullable=True))
    _point_trainees_to_their_versions(bind)
    with op.batch_alter_table("trainees") as batch_op:
        batch_op.drop_column("training_plan")
        batch_op.create_foreign_key(
            "fk_trainees_plan_version",
            "trainee_plans",
            ["id", "plan_version_id"],
            ["trainee_id", "id"],
        )

    with op.batch_alter_table("training_checkins") as batch_op:
        batch_op.add_column(sa.Column("plan_version_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_training_checkins_plan_version",
            "trainee_plans",
            ["trainee_id", "plan_version_id"],
            ["trainee_id", "id"],
        )
    # The version that was current when each check-in was last written.
    op.execute(
        "UPDATE training_checkins SET plan_version_id = ("
        "SELECT MAX(trainee_plans.id) FROM trainee_plans "
        "WHERE trainee_plans.trainee_id = training_checkins.trainee_id "
        "AND trainee_plans.created_at <= training_checkins.updated_at)"
    )


def downgrade() -> None:
    with op.batch_alter_table("training_checkins") as batch_op:
        batch_op.drop_constraint("fk_training_checkins_plan_version", type_="foreignkey")
        batch_op.drop_column("plan_version_id")

    with op.batch_alter_table("trainees") as batch_op:
        batch_op.drop_constraint("fk_trainees_plan_version", type_="foreignkey")
        batch_op.add_column(sa.Column("training_plan", sa.JSON(), nullable=True))
    # The copy of the current plan again.
    op.execute(
        "UPDATE trainees SET training_plan = ("
        "SELECT plan FROM trainee_plans WHERE trainee_plans.id = trainees.plan_version_id)"
    )
    with op.batch_alter_table("trainees") as batch_op:
        batch_op.drop_column("plan_version_id")

    with op.batch_alter_table("trainee_plans") as batch_op:
        batch_op.drop_constraint("uq_trainee_plans_trainee_id_id", type_="unique")
        batch_op.create_index("ix_trainee_plans_trainee_id_id", ["trainee_id", "id"])
