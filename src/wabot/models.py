from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetime that behaves the same on SQLite and Postgres.

    SQLite has no timezone type, so values are stored as naive UTC and made
    aware again when read.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("naive datetime; use datetime.now(UTC)")
            value = value.astimezone(UTC)
            if dialect.name == "sqlite":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Message(Base):
    """Every inbound and outbound WhatsApp message.

    Serves as chat history and, through the unique wamid, as webhook dedup.
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    # WhatsApp message ID; Meta may deliver the same webhook more than once.
    wamid: Mapped[str | None] = mapped_column(String(128), unique=True)
    direction: Mapped[str] = mapped_column(String(3))  # "in" or "out"
    phone: Mapped[str] = mapped_column(String(20), index=True)  # trainee's wa_id
    type: Mapped[str] = mapped_column(String(20))  # text, image, template, ...
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
