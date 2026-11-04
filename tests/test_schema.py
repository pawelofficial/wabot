"""What has to stay stable: the database schema the migrations build, and the exercise IDs that
stored plans and trainings use."""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from wabot import exercises
from wabot.db import make_engine
from wabot.models import Base

ROOT = Path(__file__).parents[1]
CATALOG_IDS = ROOT / "tests" / "catalog_ids.txt"


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
