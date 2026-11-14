"""Cloud Run's mode (docs/DEPLOY.md): the database copied to the bucket after a request that
changed it (db_sync.py), and the reply made before the webhook answers (REPLY_IN_REQUEST)."""

import sqlite3

import pytest

from wabot import assistant, db_sync, main, telegram
from wabot.config import Settings, get_settings

from .test_telegram import connect, post, update
from .test_webhook import add_trainee, weight_output


@pytest.fixture
def uploads(monkeypatch) -> list[tuple[str, bytes]]:
    """db_sync.upload replaced: records (bucket, data); the state of the last upload reset."""
    sent = []
    monkeypatch.setattr(db_sync, "upload", lambda bucket, data: sent.append((bucket, data)))
    monkeypatch.setattr(db_sync, "_uploaded", None)
    return sent


def rows(data: bytes, tmp_path) -> list:
    copy = tmp_path / "uploaded.db"
    copy.write_bytes(data)
    with sqlite3.connect(copy) as connection:
        found = connection.execute("SELECT x FROM t ORDER BY x").fetchall()
    connection.close()
    return [x for (x,) in found]


def test_the_database_is_uploaded_only_after_a_change(tmp_path, uploads):
    path = tmp_path / "wabot.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE t (x)")
    connection.commit()
    settings = Settings(database_url=f"sqlite:///{path.as_posix()}", db_bucket="yanek-db")

    assert db_sync.upload_if_changed(settings)  # the first time
    assert not db_sync.upload_if_changed(settings)  # nothing changed
    connection.execute("SELECT * FROM t").fetchall()
    assert not db_sync.upload_if_changed(settings)  # a read changes nothing
    connection.execute("INSERT INTO t VALUES (1)")
    connection.commit()
    assert db_sync.upload_if_changed(settings)
    connection.close()

    # A consistent copy with what was still only in the -wal file.
    [(bucket, _), (_, data)] = uploads
    assert bucket == "yanek-db" and rows(data, tmp_path) == [1]


def test_a_failed_upload_is_tried_again_next_time(tmp_path, monkeypatch, caplog):
    path = tmp_path / "wabot.db"
    sqlite3.connect(path).close()
    settings = Settings(database_url=f"sqlite:///{path.as_posix()}", db_bucket="yanek-db")
    monkeypatch.setattr(db_sync, "_uploaded", None)

    def down(bucket, data):
        raise ConnectionError("storage.googleapis.com unreachable")

    monkeypatch.setattr(db_sync, "upload", down)
    assert not db_sync.upload_if_changed(settings)
    assert "Uploading the database to gs://yanek-db failed" in caplog.text
    monkeypatch.setattr(db_sync, "upload", lambda bucket, data: None)
    assert db_sync.upload_if_changed(settings)


def test_without_a_bucket_nothing_is_uploaded(uploads):
    assert not db_sync.upload_if_changed(Settings())
    assert uploads == []


@pytest.fixture
def cloud_run(monkeypatch, uploads):
    """The app as on Cloud Run: REPLY_IN_REQUEST and DB_BUCKET on (the test database)."""
    settings = Settings(reply_in_request=True, db_bucket="yanek-db")
    main.app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    yield settings
    main.app.dependency_overrides.pop(get_settings, None)


def test_on_cloud_run_the_reply_is_sent_before_the_webhook_answers(
    client, monkeypatch, cloud_run, uploads
):
    sent = []

    def fake_send_text(settings, chat_id, text, keyboard=None, markdown=False):
        sent.append(text)
        return f"tg:{chat_id}:{900 + len(sent)}"

    monkeypatch.setattr(telegram, "send_text", fake_send_text)
    monkeypatch.setattr(
        assistant, "generate_reply", lambda *a: weight_output(82, reply="Zapisałem 82 kg.")
    )
    # No task may be left for after the response (Cloud Run takes the CPU away then).
    monkeypatch.setattr(
        main.BackgroundTasks,
        "add_task",
        lambda *a, **k: pytest.fail("a task left for after the response"),
    )
    connect(client, add_trainee())
    uploads.clear()
    post(client, update(2, "yanek waga 82"))
    assert sent[-1] == "Zapisałem 82 kg."
    # The message, the saved weight and the reply: uploaded once, at the end of the request.
    assert len(uploads) == 1


def test_a_request_that_changes_nothing_uploads_nothing(client, cloud_run, uploads):
    client.get("/")
    client.get("/")
    assert len(uploads) <= 1  # at most the first request after start (state unknown)
    uploads.clear()
    client.get("/")
    assert uploads == []
