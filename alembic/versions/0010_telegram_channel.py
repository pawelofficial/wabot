"""Telegram as a second channel next to WhatsApp (decided 2026-11-12).

trainees.telegram_chat_id: the trainee's chat with the Telegram bot, set when they share their
phone number with it. messages.channel: where a message came from or went to; every message so
far is a WhatsApp one. messages.wamid becomes external_id, the channel's message ID (a Telegram
one is "tg:<chat id>:<message id>"). messages.phone may be null: a Telegram message from someone
who isn't a linked trainee comes without a number.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("trainees") as batch_op:
        batch_op.add_column(sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True))
        batch_op.create_unique_constraint("uq_trainees_telegram_chat_id", ["telegram_chat_id"])
    with op.batch_alter_table("messages") as batch_op:
        batch_op.alter_column(
            "wamid",
            new_column_name="external_id",
            existing_type=sa.String(length=128),
            existing_nullable=True,
        )
        batch_op.alter_column("phone", existing_type=sa.String(length=20), nullable=True)
        # The default only fills the existing rows; new ones always say their channel.
        batch_op.add_column(
            sa.Column("channel", sa.String(length=10), nullable=False, server_default="whatsapp")
        )
    with op.batch_alter_table("messages") as batch_op:
        batch_op.alter_column("channel", existing_type=sa.String(length=10), server_default=None)


def downgrade() -> None:
    # Telegram messages from people who weren't linked trainees have no number; the old schema
    # needs one.
    op.execute("UPDATE messages SET phone = '' WHERE phone IS NULL")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("channel")
        batch_op.alter_column("phone", existing_type=sa.String(length=20), nullable=False)
        batch_op.alter_column(
            "external_id",
            new_column_name="wamid",
            existing_type=sa.String(length=128),
            existing_nullable=True,
        )
    with op.batch_alter_table("trainees") as batch_op:
        batch_op.drop_constraint("uq_trainees_telegram_chat_id", type_="unique")
        batch_op.drop_column("telegram_chat_id")
