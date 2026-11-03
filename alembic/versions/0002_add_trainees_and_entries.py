"""Add trainers, trainees, weight and training entries; link messages to trainees.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trainers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "trainees",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trainer_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("timezone", sa.String(length=50), nullable=False),
        sa.Column("consent_date", sa.Date(), nullable=False),
        sa.Column("training_plan", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trainer_id"], ["trainers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone"),
    )
    op.create_index("ix_trainees_trainer_id", "trainees", ["trainer_id"])

    # Existing messages keep trainee_id null; history is looked up by trainee from now on.
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("trainee_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("trainer_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_messages_trainee_id", ["trainee_id"])
        batch_op.create_foreign_key(
            "fk_messages_trainee_id_trainees", "trainees", ["trainee_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_messages_trainer_id_trainers", "trainers", ["trainer_id"], ["id"]
        )

    op.create_table(
        "weight_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trainer_id", sa.Integer(), nullable=False),
        sa.Column("trainee_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kg", sa.Double(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["trainee_id"], ["trainees.id"]),
        sa.ForeignKeyConstraint(["trainer_id"], ["trainers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trainee_id", "date"),
    )
    op.create_index("ix_weight_entries_trainer_id", "weight_entries", ["trainer_id"])

    op.create_table(
        "training_checkins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trainer_id", sa.Integer(), nullable=False),
        sa.Column("trainee_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("plan_day", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("missed_exercises", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["trainee_id"], ["trainees.id"]),
        sa.ForeignKeyConstraint(["trainer_id"], ["trainers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trainee_id", "date"),
    )
    op.create_index("ix_training_checkins_trainer_id", "training_checkins", ["trainer_id"])

    op.create_table(
        "training_exercises",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("checkin_id", sa.Integer(), nullable=False),
        sa.Column("exercise_id", sa.String(length=50), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("in_plan", sa.Boolean(), nullable=False),
        sa.Column("sets", sa.Integer(), nullable=True),
        sa.Column("reps", sa.Text(), nullable=True),
        sa.Column("weight_kg", sa.Double(), nullable=True),
        sa.Column("replaces", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["checkin_id"], ["training_checkins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_training_exercises_checkin_id", "training_exercises", ["checkin_id"])
    op.create_index("ix_training_exercises_exercise_id", "training_exercises", ["exercise_id"])


def downgrade() -> None:
    op.drop_table("training_exercises")
    op.drop_table("training_checkins")
    op.drop_table("weight_entries")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_constraint("fk_messages_trainer_id_trainers", type_="foreignkey")
        batch_op.drop_constraint("fk_messages_trainee_id_trainees", type_="foreignkey")
        batch_op.drop_index("ix_messages_trainee_id")
        batch_op.drop_column("trainer_id")
        batch_op.drop_column("trainee_id")
    op.drop_table("trainees")
    op.drop_table("trainers")
