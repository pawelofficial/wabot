"""/admin/db: SQL and a copy of the database, only with DB_ADMIN_KEY in X-Admin-Key."""

import sqlite3

import pytest

from wabot.config import Settings, get_settings
from wabot.main import app

KEY = "k" * 32


@pytest.fixture
def settings():
    settings = Settings(db_admin_key=KEY)
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.clear()


def sql(client, statement, key=KEY, **params):
    return client.post(
        "/admin/db/sql", json={"sql": statement, "params": params}, headers={"X-Admin-Key": key}
    )


def test_without_a_key_setting_there_is_no_endpoint(client):
    app.dependency_overrides[get_settings] = lambda: Settings(db_admin_key="")
    try:
        assert sql(client, "SELECT 1").status_code == 404
        assert client.get("/admin/db/file", headers={"X-Admin-Key": ""}).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_a_short_key_setting_counts_as_none(client):
    app.dependency_overrides[get_settings] = lambda: Settings(db_admin_key="short")
    try:
        assert sql(client, "SELECT 1", key="short").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_a_wrong_or_missing_key_is_refused(client, settings):
    assert sql(client, "SELECT 1", key="x" * 32).status_code == 401
    assert client.post("/admin/db/sql", json={"sql": "SELECT 1"}).status_code == 401
    assert client.get("/admin/db/file").status_code == 401


def test_changes_are_committed_and_selects_return_rows(client, settings):
    insert = "INSERT INTO trainers (name, created_at) VALUES (:name, CURRENT_TIMESTAMP)"
    response = sql(client, insert, name="Ala")
    assert response.status_code == 200 and response.json()["rowcount"] == 1
    response = sql(client, "SELECT name FROM trainers WHERE name = :name", name="Ala")
    assert response.json() == {"columns": ["name"], "rows": [["Ala"]], "rowcount": 1}


def test_a_bad_statement_says_why(client, settings):
    response = sql(client, "SELECT * FROM nothing_here")
    assert response.status_code == 400
    assert "nothing_here" in response.json()["detail"]


def test_the_database_downloads_as_a_sqlite_file(client, settings, tmp_path):
    sql(client, "INSERT INTO trainers (name, created_at) VALUES ('Ala', CURRENT_TIMESTAMP)")
    response = client.get("/admin/db/file", headers={"X-Admin-Key": KEY})
    assert response.status_code == 200
    copy = tmp_path / "copy.db"
    copy.write_bytes(response.content)
    with sqlite3.connect(copy) as connection:
        assert connection.execute("SELECT name FROM trainers").fetchall() == [("Ala",)]
