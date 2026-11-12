"""What has to stay stable: the database schema the migrations build, and the exercise IDs that
stored plans and trainings use."""

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from wabot import exercises
from wabot.db import make_engine
from wabot.models import Base

ROOT = Path(__file__).parents[1]
CATALOG_IDS = ROOT / "tests" / "catalog_ids.txt"
# A created_at for rows written by hand, as UTCDateTime stores it on SQLite.
T0 = {"t": "2026-10-01 08:00:00.000000"}


def snapshot_ids() -> list[str]:
    lines = CATALOG_IDS.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def test_no_exercise_id_is_ever_removed():
    # A plan or a saved training with a removed ID no longer validates: the bot apologizes on
    # every message of those trainees and their plan pages fail.
    missing = [i for i in snapshot_ids() if i not in exercises.IDS]
    assert missing == [], f"removed from exercises.yaml (IDs are permanent): {missing}"


def test_new_exercise_ids_are_in_the_snapshot():
    new = [i for i in exercises.IDS if i not in snapshot_ids()]
    assert new == [], f"add the new IDs to the end of {CATALOG_IDS.name}: {new}"


@pytest.fixture
def migrated_url(tmp_path, monkeypatch):
    """An empty SQLite database and an alembic config that migrates it (not alembic.ini: its
    logging setup would silence the app's loggers)."""
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("DATABASE_URL", url)  # alembic/env.py reads it
    alembic = Config()
    alembic.set_main_option("script_location", str(ROOT / "alembic"))
    return url, alembic


def schema_differences(url: str) -> list:
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            return compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()


def test_the_migrations_build_the_models_schema(migrated_url):
    url, alembic = migrated_url
    command.upgrade(alembic, "head")
    assert schema_differences(url) == []


def test_every_migration_can_be_undone_and_redone(migrated_url):
    url, alembic = migrated_url
    command.upgrade(alembic, "head")
    command.downgrade(alembic, "base")
    command.upgrade(alembic, "head")
    assert schema_differences(url) == []


def test_migration_0007_points_trainees_and_checkins_to_their_versions(migrated_url):
    """Data in the old shape (a plan copied onto the trainee) through the migration: each trainee
    points to the version with exactly the plan they had, each check-in to the version current
    when it was saved, and downgrading puts the copies back."""
    url, alembic = migrated_url
    plan = {"days": [{"name": "A", "exercises": [{"id": "plank"}]}]}
    other = {"days": [{"name": "B", "exercises": [{"id": "push_up"}]}]}
    command.upgrade(alembic, "0005")
    engine = make_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO trainers (id, name, created_at) VALUES (1, 'T', :t)"), T0)
        for trainee_id, stored in [(1, plan), (2, plan), (3, plan), (4, None), (5, "null")]:
            db.execute(
                text(
                    "INSERT INTO trainees (id, trainer_id, phone, name, timezone, consent_date,"
                    " training_plan, created_at) VALUES (:id, 1, :phone, 'X', 'Europe/Warsaw',"
                    " '2026-10-01', :plan, :t)"
                ),
                {
                    "id": trainee_id,
                    "phone": f"4800000000{trainee_id}",
                    "plan": stored if stored in (None, "null") else json.dumps(stored),
                    **T0,
                },
            )
    command.upgrade(alembic, "0006")  # every non-null copy becomes an "imported" version
    with engine.begin() as db:
        # 2: the copy changed after its version (written around plan_history); 3: plan removed
        # (as add_trainee.py --no-plan did); 5: JSON null, imported as a version by 0006.
        db.execute(
            text("UPDATE trainees SET training_plan = :p WHERE id = 2"), {"p": json.dumps(other)}
        )
        db.execute(text("UPDATE trainees SET training_plan = NULL WHERE id = 3"))
        for checkin_id, saved_at in [
            (1, "2026-09-01 10:00:00.000000"),
            (2, "2026-11-02 10:00:00.000000"),
        ]:
            db.execute(
                text(
                    "INSERT INTO training_checkins (id, trainer_id, trainee_id, date, status,"
                    " missed_exercises, created_at, updated_at) VALUES (:id, 1, 1, '2026-10-02',"
                    " 'done', '[]', :t, :t)"
                ),
                {"id": checkin_id, "t": saved_at},
            )
    command.upgrade(alembic, "0007")

    with engine.connect() as db:
        pointed = dict(db.execute(text("SELECT id, plan_version_id FROM trainees")).all())
        version_plans = {
            v: (trainee, json.loads(p), source)
            for v, trainee, p, source in db.execute(
                text("SELECT id, trainee_id, plan, source FROM trainee_plans")
            )
        }
        checkins = dict(db.execute(text("SELECT id, plan_version_id FROM training_checkins")).all())
    assert version_plans[pointed[1]] == (1, plan, "imported")  # the version 0006 made
    assert version_plans[pointed[2]] == (2, other, "imported")  # a new one with the copy
    assert len([v for v in version_plans.values() if v[0] == 2]) == 2
    assert pointed[3] is None  # no plan; its version stays in the history
    assert any(v[0] == 3 for v in version_plans.values())
    assert pointed[4] is None and pointed[5] is None
    assert not any(v[0] == 5 for v in version_plans.values())  # the JSON null "version" is gone
    assert checkins == {1: None, 2: pointed[1]}  # before Kasia's first version / after it

    command.downgrade(alembic, "0006")
    with engine.connect() as db:
        copies = dict(db.execute(text("SELECT id, training_plan FROM trainees")).all())
    engine.dispose()
    assert json.loads(copies[1]) == plan and json.loads(copies[2]) == other
    assert copies[3] is None and copies[4] is None


def test_migration_0008_gives_plan_days_ids_that_follow_the_day(migrated_url):
    """Plans without day IDs through the migration: a version copied from the library takes
    its days' IDs, a later version keeps them for days of the same name, and check-ins get the
    ID of their day; downgrading takes the IDs out again."""
    url, alembic = migrated_url
    day = lambda name: {"name": name, "exercises": [{"id": "plank"}]}  # noqa: E731
    library_plan = {"days": [day("Trening 1"), day("Trening 2")]}
    copied = {"days": [day("trening 1"), day("Trening 2")]}  # the same days, as assigned
    edited = {"days": [day("Trening 1"), day("Nogi"), day("Trening 3")]}  # 2 renamed, 3 new
    command.upgrade(alembic, "0007")
    engine = make_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO trainers (id, name, created_at) VALUES (1, 'T', :t)"), T0)
        db.execute(
            text(
                "INSERT INTO plan_templates (id, trainer_id, name, plan, created_at, updated_at)"
                " VALUES (1, 1, 'FBW', :p, :t, :t)"
            ),
            {"p": json.dumps(library_plan), **T0},
        )
        db.execute(
            text(
                "INSERT INTO trainees (id, trainer_id, phone, name, timezone, consent_date,"
                " created_at) VALUES (1, 1, '48000000001', 'X', 'Europe/Warsaw', '2026-10-01', :t)"
            ),
            T0,
        )
        for version_id, plan, source, template_id in [
            (1, copied, "template", 1),
            (2, edited, "edited", 1),
        ]:
            db.execute(
                text(
                    "INSERT INTO trainee_plans (id, trainer_id, trainee_id, plan, source,"
                    " template_id, name, created_at) VALUES (:id, 1, 1, :p, :s, :tid, 'FBW', :t)"
                ),
                {"id": version_id, "p": json.dumps(plan), "s": source, "tid": template_id, **T0},
            )
        db.execute(text("UPDATE trainees SET plan_version_id = 2"))
        for checkin_id, version_id, plan_day in [
            (1, 1, "Trening 2"),
            (2, 2, "Trening 1"),
            (3, 2, "Nogi"),
            (4, None, None),
        ]:
            db.execute(
                text(
                    "INSERT INTO training_checkins (id, trainer_id, trainee_id, date, status,"
                    " missed_exercises, plan_version_id, plan_day, created_at, updated_at)"
                    " VALUES (:id, 1, 1, '2026-10-02', 'done', '[]', :v, :d, :t, :t)"
                ),
                {"id": checkin_id, "v": version_id, "d": plan_day, **T0},
            )
    command.upgrade(alembic, "0008")

    def day_ids(db, table, row_id):
        plan = json.loads(
            db.execute(text(f"SELECT plan FROM {table} WHERE id = {row_id}")).scalar()
        )
        return {d["name"]: d["id"] for d in plan["days"]}

    with engine.connect() as db:
        library = day_ids(db, "plan_templates", 1)
        v1, v2 = day_ids(db, "trainee_plans", 1), day_ids(db, "trainee_plans", 2)
        checkins = dict(db.execute(text("SELECT id, plan_day_id FROM training_checkins")).all())
    assert v1 == {"trening 1": library["Trening 1"], "Trening 2": library["Trening 2"]}
    assert v2["Trening 1"] == library["Trening 1"]  # the same day in the next version
    ids = [*library.values(), v2["Nogi"], v2["Trening 3"]]
    assert len(set(ids)) == 4  # a renamed or new day gets a new ID, never an old one
    assert checkins == {1: library["Trening 2"], 2: library["Trening 1"], 3: v2["Nogi"], 4: None}

    command.downgrade(alembic, "0007")
    with engine.connect() as db:
        plans_left = [
            json.loads(p) for p in db.execute(text("SELECT plan FROM trainee_plans")).scalars()
        ]
        plans_left.append(json.loads(db.execute(text("SELECT plan FROM plan_templates")).scalar()))
    engine.dispose()
    assert plans_left == [copied, edited, library_plan]


def test_migration_0009_tells_training_outside_the_plan_from_the_plans(migrated_url):
    url, alembic = migrated_url
    command.upgrade(alembic, "0008")
    engine = make_engine(url)
    # (plan day, status, exercises as (in_plan, replaces)) → outside the plan
    rows = {
        1: ("Dzień A", "done", [(True, None)], False),
        2: (None, "done", [(False, None)], True),  # a run
        3: (None, "skipped", [], False),
        4: (None, "done", [(False, None), (True, None)], False),  # the day wasn't known
        5: (None, "done", [(False, "back_squat")], False),  # a replacement, the same
    }
    with engine.begin() as db:
        db.execute(text("INSERT INTO trainers (id, name, created_at) VALUES (1, 'T', :t)"), T0)
        db.execute(
            text(
                "INSERT INTO trainees (id, trainer_id, phone, name, timezone, consent_date,"
                " created_at) VALUES (1, 1, '48000000001', 'X', 'Europe/Warsaw', '2026-10-01', :t)"
            ),
            T0,
        )
        for checkin_id, (plan_day, status, exercises_, _) in rows.items():
            db.execute(
                text(
                    "INSERT INTO training_checkins (id, trainer_id, trainee_id, date, status,"
                    " missed_exercises, plan_day, created_at, updated_at)"
                    " VALUES (:id, 1, 1, '2026-10-02', :s, '[]', :d, :t, :t)"
                ),
                {"id": checkin_id, "s": status, "d": plan_day, **T0},
            )
            for in_plan, replaces in exercises_:
                db.execute(
                    text(
                        "INSERT INTO training_exercises (checkin_id, name, in_plan, replaces)"
                        " VALUES (:c, 'x', :p, :r)"
                    ),
                    {"c": checkin_id, "p": in_plan, "r": replaces},
                )
    command.upgrade(alembic, "0009")
    with engine.connect() as db:
        outside = dict(db.execute(text("SELECT id, outside_plan FROM training_checkins")).all())
        exercises_left = db.execute(text("SELECT count(*) FROM training_exercises")).scalar()
    engine.dispose()
    assert outside == {i: row[3] for i, row in rows.items()}
    assert exercises_left == 5  # the check-ins' exercises are still there


def test_migration_0010_keeps_whatsapp_messages_and_adds_telegram(migrated_url):
    url, alembic = migrated_url
    command.upgrade(alembic, "0009")
    engine = make_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO trainers (id, name, created_at) VALUES (1, 'T', :t)"), T0)
        for trainee_id, phone in ((1, "48000000001"), (2, "48000000002")):
            db.execute(
                text(
                    "INSERT INTO trainees (id, trainer_id, phone, name, timezone, consent_date,"
                    " created_at) VALUES (:id, 1, :p, 'X', 'Europe/Warsaw', '2026-10-01', :t)"
                ),
                {"id": trainee_id, "p": phone, **T0},
            )
        db.execute(
            text(
                "INSERT INTO messages (id, wamid, direction, phone, type, body, trainee_id,"
                " created_at) VALUES (1, 'wamid.A', 'in', '48000000001', 'text', 'yanek 80', 1, :t)"
            ),
            T0,
        )
    command.upgrade(alembic, "0010")
    with engine.connect() as db:
        rows = db.execute(text("SELECT id, channel, external_id, body FROM messages")).all()
    assert rows == [(1, "whatsapp", "wamid.A", "yanek 80")]

    insert = text(
        "INSERT INTO messages (channel, external_id, direction, phone, type, created_at)"
        " VALUES ('telegram', :e, 'in', NULL, 'text', :t)"
    )
    with engine.begin() as db:
        db.execute(insert, {"e": "tg:5:1", **T0})  # from someone who isn't a trainee: no number
    # Still the dedup: an ID only once.
    with pytest.raises(IntegrityError), engine.begin() as db:
        db.execute(insert, {"e": "tg:5:1", **T0})
    # A Telegram chat belongs to one trainee; Telegram's IDs don't fit 32 bits.
    link = text("UPDATE trainees SET telegram_chat_id = :c WHERE id = :id")
    with engine.begin() as db:
        db.execute(link, {"c": 7_000_000_000, "id": 1})
    with pytest.raises(IntegrityError), engine.begin() as db:
        db.execute(link, {"c": 7_000_000_000, "id": 2})
    with engine.connect() as db:
        assert db.execute(text("SELECT telegram_chat_id FROM trainees WHERE id = 1")).scalar() == (
            7_000_000_000
        )
    engine.dispose()

    command.downgrade(alembic, "0009")
    engine = make_engine(url)
    with engine.connect() as db:
        rows = db.execute(text("SELECT wamid, phone FROM messages ORDER BY id")).all()
    engine.dispose()
    assert rows == [("wamid.A", "48000000001"), ("tg:5:1", "")]
