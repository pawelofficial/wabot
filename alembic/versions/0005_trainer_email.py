"""Trainers get the email of the Google account they sign in to the dashboard with.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trainers", sa.Column("email", sa.String(length=254), nullable=True))
    op.create_index("ix_trainers_email", "trainers", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_trainers_email", table_name="trainers")
    with op.batch_alter_table("trainers") as batch_op:
        batch_op.drop_column("email")
