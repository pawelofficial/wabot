"""The trainer's plan library (plan_templates) and every version of a trainee's plan
(trainee_plans). Plans already on trainees become their first version.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trainer_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trainer_id"], ["trainers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trainer_id", "name"),
    )
    op.create_index("ix_plan_templates_trainer_id", "plan_templates", ["trainer_id"])
    op.create_table(
        "trainee_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trainer_id", sa.Integer(), nullable=False),
        sa.Column("trainee_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trainer_id"], ["trainers.id"]),
        sa.ForeignKeyConstraint(["trainee_id"], ["trainees.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["plan_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trainee_plans_trainer_id", "trainee_plans", ["trainer_id"])
    op.create_index("ix_trainee_plans_trainee_id_id", "trainee_plans", ["trainee_id", "id"])
    # The plans trainees have now are their first version ("imported": from before the history).
    op.execute(
        "INSERT INTO trainee_plans (trainer_id, trainee_id, plan, source, created_at) "
        "SELECT trainer_id, id, training_plan, 'imported', created_at FROM trainees "
        "WHERE training_plan IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_trainee_plans_trainee_id_id", table_name="trainee_plans")
    op.drop_index("ix_trainee_plans_trainer_id", table_name="trainee_plans")
    op.drop_table("trainee_plans")
    op.drop_index("ix_plan_templates_trainer_id", table_name="plan_templates")
    op.drop_table("plan_templates")
