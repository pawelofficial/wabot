"""scripts/setup_db.py: a new database with the migrations and the simulated month, and adding it
to an existing database again is refused (after a backup)."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def setup_db(*args: str, database_url: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if database_url:
        env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "scripts/setup_db.py", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_a_new_database_with_the_simulated_month(tmp_path):
    db = tmp_path / "dev.db"
    done = setup_db("--new", str(db), "--simulated")
    assert done.returncode == 0, done.stderr
    assert "Trener Marek (marek.trener@example.com): Kasia, Tomek; 2 plans in the library" in (
        done.stdout
    )
    assert f"DATABASE_URL=sqlite:///{db.as_posix()}" in done.stdout
    with sqlite3.connect(db) as connection:
        trainees = connection.execute("SELECT count(*) FROM trainees").fetchone()[0]
        templates = connection.execute(
            "SELECT count(*) FROM trainee_plans WHERE template_id IS NOT NULL"
        ).fetchone()[0]
        replies = connection.execute(
            "SELECT count(*) FROM messages WHERE direction = 'out'"
        ).fetchone()[0]
    connection.close()
    assert (trainees, templates) == (3, 3)  # every trainee's plan comes from the library
    assert replies > 0

    # Pointed at it (DATABASE_URL), the simulated month isn't added a second time.
    again = setup_db("--simulated", database_url=f"sqlite:///{db.as_posix()}")
    assert again.returncode != 0
    assert "already there (Kasia, Ola, Tomek)" in again.stderr
    assert len(list((tmp_path / "db_backups").glob("dev-*.db"))) == 1


def test_new_refuses_an_existing_file(tmp_path):
    db = tmp_path / "taken.db"
    db.write_bytes(b"")
    done = setup_db("--new", str(db))
    assert done.returncode != 0 and "already exists" in done.stderr


def test_an_empty_database(tmp_path):
    db = tmp_path / "empty.db"
    done = setup_db("--new", str(db))
    assert done.returncode == 0, done.stderr
    with sqlite3.connect(db) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    connection.close()
    assert {"trainers", "trainees", "messages", "plan_templates"} <= tables
