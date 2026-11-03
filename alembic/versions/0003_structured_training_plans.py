"""Training plans become JSON (days of catalog exercises with replacements).

Free-text plans can't be converted, so they are cleared: register the plan again with
`uv run python scripts/add_trainee.py --plan plans/example.yaml`.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE trainees SET training_plan = NULL")
    with op.batch_alter_table("trainees") as batch_op:
        batch_op.alter_column(
            "training_plan",
            existing_type=sa.Text(),
            type_=sa.JSON(),
            existing_nullable=True,
            postgresql_using="training_plan::json",
        )
    with op.batch_alter_table("training_exercises") as batch_op:
        batch_op.alter_column(
            "replaces", existing_type=sa.Text(), type_=sa.String(length=50), existing_nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("training_exercises") as batch_op:
        batch_op.alter_column(
            "replaces", existing_type=sa.String(length=50), type_=sa.Text(), existing_nullable=True
        )
    with op.batch_alter_table("trainees") as batch_op:
        batch_op.alter_column(
            "training_plan",
            existing_type=sa.JSON(),
            type_=sa.Text(),
            existing_nullable=True,
            postgresql_using="training_plan::text",
        )
