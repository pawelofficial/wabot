"""messages.handled_at: when the bot dealt with a trainee's message (decided 2026-11-13).

Until now "already handled" was guessed: everything before the bot's last message counted as
saved. A reply that failed to send, an apology or a fixed text (the help after /start) moved that
line, so reports were saved twice or lost. Now an inbound message is handled when a reply has
saved what it reported (conversation.load_turn() / claim(); docs/CONVERSATION.md), and it waits
for the next "yanek" until then.

Existing messages get the old rule once: a trainee's inbound message is handled at the time of
the first bot message after it; the ones after the bot's last message stay waiting, as they
would have been new under the old rule too.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

messages = sa.table(
    "messages",
    sa.column("id", sa.Integer),
    sa.column("direction", sa.String),
    sa.column("trainee_id", sa.Integer),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("handled_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True))
    later = messages.alias("later")
    first_bot_message_after = (
        sa.select(sa.func.min(later.c.created_at))
        .where(
            later.c.trainee_id == messages.c.trainee_id,
            later.c.direction == "out",
            later.c.id > messages.c.id,
        )
        .scalar_subquery()
    )
    op.execute(
        messages.update()
        .where(messages.c.direction == "in", messages.c.trainee_id.is_not(None))
        .values(handled_at=first_bot_message_after)
    )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("handled_at")
