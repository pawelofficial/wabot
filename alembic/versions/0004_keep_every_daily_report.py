"""A day can have several weight and training entries (DAILY_UPDATES_ONLY decides in the code).

The unique (trainee_id, date) constraints become plain indexes. Downgrading needs one entry per
day again, so it deletes every entry of a day but the newest.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("weight_entries", "training_checkins")


def _unique_name(table: str) -> str:
    # 0002 created the constraints without a name. Postgres names them <table>_<cols>_key; for
    # SQLite, batch mode gives the reflected constraint this name through naming_convention.
    if op.get_context().dialect.name == "postgresql":
        return f"{table}_trainee_id_date_key"
    return f"uq_{table}_trainee_id_date"


NAMING = {"uq": "uq_%(table_name)s_%(column_0_N_name)s"}


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table, naming_convention=NAMING) as batch_op:
            batch_op.drop_constraint(_unique_name(table), type_="unique")
            batch_op.create_index(f"ix_{table}_trainee_id_date", ["trainee_id", "date"])


def downgrade() -> None:
    newest = "id NOT IN (SELECT MAX(id) FROM {table} GROUP BY trainee_id, date)"  # noqa: S608
    # Exercises first: foreign keys (and so ON DELETE CASCADE) are off while migrating SQLite.
    op.execute(
        "DELETE FROM training_exercises WHERE checkin_id IN "
        f"(SELECT id FROM training_checkins WHERE {newest.format(table='training_checkins')})"
    )
    for table in TABLES:
        op.execute(f"DELETE FROM {table} WHERE {newest.format(table=table)}")
        with op.batch_alter_table(table, naming_convention=NAMING) as batch_op:
            batch_op.drop_index(f"ix_{table}_trainee_id_date")
            batch_op.create_unique_constraint(_unique_name(table), ["trainee_id", "date"])
