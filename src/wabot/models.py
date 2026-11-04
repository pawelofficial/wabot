import datetime as dt

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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
    # The Google account the trainer signs in to the dashboard with, lowercase.
    email: Mapped[str | None] = mapped_column(String(254), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class Trainee(Base):
    """A trainer's client. The bot finds the trainee behind a message by the phone number."""

    __tablename__ = "trainees"
    __table_args__ = (
        # The current plan must be one of the trainee's own versions: (id, plan_version_id) is a
        # (trainee_plans.trainee_id, trainee_plans.id) pair. trainees and trainee_plans point at
        # each other, so this one is created after both tables (use_alter; SQLite has it inline).
        ForeignKeyConstraint(
            ["id", "plan_version_id"],
            ["trainee_plans.trainee_id", "trainee_plans.id"],
            name="fk_trainees_plan_version",
            use_alter=True,
        ),
    )

    # autoincrement: the id is also part of the foreign key above, which would otherwise make
    # SQLAlchemy expect it to be given rather than generated.
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainers.id"), index=True)
    # WhatsApp's form: digits only, with the country code (48123456789).
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    # The trainee's local time decides what "today" and "wczoraj" mean.
    timezone: Mapped[str] = mapped_column(String(50), default=DEFAULT_TIMEZONE)
    # The day the trainee signed the paper consent, which the trainer keeps on file.
    consent_date: Mapped[dt.date]
    # The current training plan: one of the trainee's versions (trainee_plans), set only by
    # plan_history; null when the trainee has no plan. The plan itself is stored once, in the
    # version; plans.of(trainee) reads it for the bot's prompt and the dashboard.
    plan_version_id: Mapped[int | None]
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    trainer: Mapped[Trainer] = relationship()
    # Loaded together with the trainee (lazy="joined"), so the plan can be read after the
    # session is closed (main.reply() reads the trainee, then calls the LLM). post_update: the
    # trainee is written first, then the version, then the pointer.
    plan_version: Mapped["TraineePlan | None"] = relationship(
        primaryjoin="Trainee.plan_version_id == TraineePlan.id",
        foreign_keys="Trainee.plan_version_id",
        post_update=True,
        lazy="joined",
    )

    def __init__(self, **fields):
        # A new trainee has no plan until plan_history.assign() gives one, and plans.of() can
        # say so even after the session is closed (nothing left to load).
        fields.setdefault("plan_version", None)
        super().__init__(**fields)


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
    """A trainee's body weight on a day, as reported. A day can have several (corrections are
    new rows unless DAILY_UPDATES_ONLY); the newest one is the current value."""

    __tablename__ = "weight_entries"
    __table_args__ = (Index("ix_weight_entries_trainee_id_date", "trainee_id", "date"),)

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
    """A trainee's training on a day, as reported, with its exercises. A day can have several
    (see WeightEntry); the newest one is the current session."""

    __tablename__ = "training_checkins"
    __table_args__ = (
        Index("ix_training_checkins_trainee_id_date", "trainee_id", "date"),
        # The plan version must be one of the trainee's own.
        ForeignKeyConstraint(
            ["trainee_id", "plan_version_id"],
            ["trainee_plans.trainee_id", "trainee_plans.id"],
            name="fk_training_checkins_plan_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainers.id"), index=True)
    trainee_id: Mapped[int] = mapped_column(ForeignKey("trainees.id"))
    date: Mapped[dt.date]
    # The trainee's plan version when this was saved: the plan the report was checked against,
    # which plan_day and missed_exercises refer to (the plan can change later). Null when the
    # trainee had no plan.
    plan_version_id: Mapped[int | None]
    # The plan day it was: its ID (plans.PlanDay.id), which stays the same when the trainer
    # renames the day or changes the plan around it, so trainings of one day can be found across
    # plan versions; and its name exactly as in that version, as the trainee saw it. Both null
    # for training outside the plan.
    plan_day_id: Mapped[str | None] = mapped_column(String(32))
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


class PlanTemplate(Base):
    """A plan in the trainer's library (Biblioteka planów), not tied to a trainee. Assigning it
    to a trainee copies it (a TraineePlan), so editing it later doesn't change their plan."""

    __tablename__ = "plan_templates"
    __table_args__ = (UniqueConstraint("trainer_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainers.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    plan: Mapped[dict] = mapped_column(JSON)  # as plans.to_db() writes it
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class TraineePlan(Base):
    """One version of a trainee's training plan. Every change (an edit, a plan from the library,
    a restored older version) adds a row, and trainees.plan_version_id points to the current one
    (the newest, unless the plan was removed). Written only by plan_history. Check-ins point to
    the version they were saved against."""

    __tablename__ = "trainee_plans"
    # (trainee_id, id) is unique anyway (id is); the constraint lets trainees and
    # training_checkins point to "this trainee's version" with a foreign key, and its index serves
    # the history (a trainee's versions by id).
    __table_args__ = (UniqueConstraint("trainee_id", "id", name="uq_trainee_plans_trainee_id_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("trainers.id"), index=True)
    trainee_id: Mapped[int] = mapped_column(ForeignKey("trainees.id"))
    plan: Mapped[dict] = mapped_column(JSON)
    # How this version came about: plan_history.SOURCES.
    source: Mapped[str] = mapped_column(String(20))
    # The library plan it was copied from or is based on (kept through edits); its name at the
    # time, so the history still says it after the library plan is renamed or deleted.
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_templates.id", ondelete="SET NULL")
    )
    name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
