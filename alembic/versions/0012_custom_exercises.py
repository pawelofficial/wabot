"""custom_exercises: exercises trainers add themselves (decided 2026-11-19).

The built-in catalog (exercises.yaml) has a fixed set; a trainer whose plan needs an exercise it
doesn't have adds it in the dashboard (Baza ćwiczeń). Each row belongs to one trainer, and its
exercise_id is used like a catalog ID in plans and trainings.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_exercises",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trainer_id", sa.Integer(), nullable=False),
        sa.Column("exercise_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("group", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trainer_id"], ["trainers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exercise_id"),
    )
    op.create_index(
        op.f("ix_custom_exercises_trainer_id"), "custom_exercises", ["trainer_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_custom_exercises_trainer_id"), table_name="custom_exercises")
    op.drop_table("custom_exercises")
