import datetime as dt

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
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
            value = value.astimezone(dt.UTC)
            if dialect.name == "sqlite":
                value = value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return value


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


DEFAULT_TIMEZONE = "Europe/Warsaw"


class Base(DeclarativeBase):
    pass


# Text the LLM writes (exercise names, notes) is Text rather than String(n), so an unexpectedly
# long value can't make an insert fail on Postgres.


class Trainer(Base):
    """A personal trainer. Created by hand for now (scripts/add_trainee.py)."""

    __tablename__ = "trainers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class Trainee(Base):
    """A trainer's client. The bot finds the trainee behind a message by the phone number."""

    __tablename__ = "trainees"

    id: Mapped[int] = mapped_column(primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainers.id"), index=True)
    # WhatsApp's form: digits only, with the country code (48123456789).
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    # The trainee's local time decides what "today" and "wczoraj" mean.
    timezone: Mapped[str] = mapped_column(String(50), default=DEFAULT_TIMEZONE)
    # The day the trainee signed the paper consent, which the trainer keeps on file.
    consent_date: Mapped[dt.date]
    # Days of catalog exercises with their replacements, as JSON; read and checked through
    # plans.of(trainee), injected into the LLM's prompt.
    training_plan: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    trainer: Mapped[Trainer] = relationship()


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
    # Null for messages from numbers that aren't registered: only what dedup needs is kept.
    body: Mapped[str | None] = mapped_column(Text)
    trainee_id: Mapped[int | None] = mapped_column(ForeignKey("trainees.id"), index=True)
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey("trainers.id"))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class WeightEntry(Base):
    """A trainee's body weight on a day. One per day: a newer report replaces it."""

    __tablename__ = "weight_entries"
    __table_args__ = (UniqueConstraint("trainee_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainers.id"), index=True)
    trainee_id: Mapped[int] = mapped_column(ForeignKey("trainees.id"))
    date: Mapped[dt.date]
    kg: Mapped[float]
    # The trainee's message the bot answered when it saved this (the "yanek ..." one).
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class TrainingCheckin(Base):
    """A trainee's training on a day, as reported. One per day: a newer report replaces it,
    exercises included."""

    __tablename__ = "training_checkins"
    __table_args__ = (UniqueConstraint("trainee_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainers.id"), index=True)
    trainee_id: Mapped[int] = mapped_column(ForeignKey("trainees.id"))
    date: Mapped[dt.date]
    # The day's name exactly as in the plan; null for training outside the plan.
    plan_day: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10))  # done, partial or skipped
    # Catalog IDs of the day's plan exercises the trainee said they didn't do.
    missed_exercises: Mapped[list[str]] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    # The trainee's message the bot answered when it saved this (the "yanek ..." one).
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    # Replacing the list deletes the old rows (delete-orphan).
    exercises: Mapped[list["TrainingExercise"]] = relationship(
        cascade="all, delete-orphan", order_by="TrainingExercise.id"
    )


class TrainingExercise(Base):
    """One exercise of a training check-in. The columns match llm_output.Exercise.

    Reached only through its check-in, which carries trainer_id and trainee_id.
    """

    __tablename__ = "training_exercises"

    id: Mapped[int] = mapped_column(primary_key=True)
    checkin_id: Mapped[int] = mapped_column(
        ForeignKey("training_checkins.id", ondelete="CASCADE"), index=True
    )
    # Catalog ID from exercises.yaml (progress is charted by it); null outside the catalog.
    # Plan exercises and their replacements get it from the LLM, others from the name.
    exercise_id: Mapped[str | None] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(Text)
    in_plan: Mapped[bool]
    sets: Mapped[int | None]
    reps: Mapped[str | None] = mapped_column(Text)  # "8" or "8,8,7,6"
    weight_kg: Mapped[float | None]
    # Catalog ID of the plan exercise this one was done instead of ("suwnica zamiast przysiadów").
    replaces: Mapped[str | None] = mapped_column(String(50))
    comment: Mapped[str | None] = mapped_column(Text)
