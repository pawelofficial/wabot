"""The SQLite database kept in a Cloud Storage bucket between starts (DB_BUCKET; Cloud Run,
docs/DEPLOY.md).

Cloud Run's disk is gone when an instance stops, so deploy/start.sh downloads the database at
start (deploy/fetch_db.py), and after every request that changed it, main.py's middleware calls
upload_if_changed(): a consistent copy (SQLite's backup API) goes to gs://<bucket>/wabot.db
before the response is sent. The service runs as one instance at most, so there's one writer.

"Changed" is read from the files: every commit writes to the -wal file (or the database), a read
doesn't. A checkpoint changes the file without new data, which only costs an extra upload.
"""

import logging
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

from sqlalchemy.engine import make_url

from wabot.config import Settings

log = logging.getLogger(__name__)

OBJECT = "wabot.db"
SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"

_lock = threading.Lock()
_uploaded: tuple | None = None  # the files' state at the last upload
_session = None  # google.auth's AuthorizedSession, made on first use


def database_file(settings: Settings) -> Path:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise ValueError("DB_BUCKET needs a SQLite DATABASE_URL")
    return Path(url.database)


def _state(path: Path) -> tuple:
    """What changes with every commit: the database's and its -wal file's size and time."""
    found = []
    for file in (path, Path(f"{path}-wal")):
        try:
            stat = file.stat()
            found.append((stat.st_size, stat.st_mtime_ns))
        except FileNotFoundError:
            found.append(None)
    return tuple(found)


def snapshot(path: Path) -> bytes:
    """A consistent copy of the database as one file (what's in the -wal file included)."""
    fd, copy = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        source, target = sqlite3.connect(path), sqlite3.connect(copy)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        return Path(copy).read_bytes()
    finally:
        os.unlink(copy)


def upload(bucket: str, data: bytes) -> None:
    """Replace gs://<bucket>/wabot.db (the service account's own credentials)."""
    global _session
    if _session is None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(scopes=[SCOPE])
        _session = AuthorizedSession(credentials)
    response = _session.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
        params={"uploadType": "media", "name": OBJECT},
        data=data,
        headers={"Content-Type": "application/octet-stream"},
        timeout=30,
    )
    response.raise_for_status()


def upload_if_changed(settings: Settings) -> bool:
    """Upload the database if it changed since the last upload. Returns whether it uploaded.
    A failure is logged and tried again after the next request (the state isn't marked)."""
    global _uploaded
    if not settings.db_bucket:
        return False
    path = database_file(settings)
    with _lock:
        state = _state(path)
        if state == _uploaded:
            return False
        try:
            data = snapshot(path)
            upload(settings.db_bucket, data)
        except Exception:
            log.exception("Uploading the database to gs://%s failed", settings.db_bucket)
            return False
        # The state from before the copy: a commit during it uploads again next time.
        _uploaded = state
        log.info(
            "Database uploaded to gs://%s/%s (%d bytes)", settings.db_bucket, OBJECT, len(data)
        )
        return True
