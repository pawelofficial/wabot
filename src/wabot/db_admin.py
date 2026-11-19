"""Changing the database by hand, over HTTP: the running bot's own database (on Cloud Run the
SQLite file the instance has, which the bucket middleware then uploads, db_sync.py). For fixes
the dashboard has no page for; scripts/db_admin.py is the client.

    POST /admin/db/sql   {"sql": "...", "params": {...}}  one statement, committed; rows back
    GET  /admin/db/file  a consistent copy of the SQLite file

Every request needs the header X-Admin-Key: DB_ADMIN_KEY. Without DB_ADMIN_KEY (or with a
short one) the endpoints don't exist (404), so nothing is open by default.
"""

import hmac
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from wabot import db_sync
from wabot.config import Settings, get_settings
from wabot.db import engine

log = logging.getLogger(__name__)

MIN_KEY_LENGTH = 24
KEY_HEADER = "X-Admin-Key"

router = APIRouter(prefix="/admin/db", include_in_schema=False)


def require_key(
    settings: Annotated[Settings, Depends(get_settings)],
    key: Annotated[str | None, Header(alias=KEY_HEADER)] = None,
) -> Settings:
    expected = settings.db_admin_key
    if len(expected) < MIN_KEY_LENGTH:
        raise HTTPException(404)
    if not key or not hmac.compare_digest(key.encode(), expected.encode()):
        log.warning("Database admin: a request with a wrong or missing key")
        raise HTTPException(401, "Wrong or missing X-Admin-Key")
    return settings


KeyDep = Annotated[Settings, Depends(require_key)]


class Statement(BaseModel):
    sql: str
    # :name placeholders in the SQL get these values.
    params: dict[str, Any] = {}


def _plain(value):
    """A value JSON can carry (BLOBs as hex)."""
    return value.hex() if isinstance(value, bytes | memoryview) else value


@router.post("/sql")
def run_sql(statement: Statement, _: KeyDep) -> dict:
    """Run one SQL statement in its own transaction and commit it. A SELECT (or anything that
    returns rows, like UPDATE ... RETURNING) gives the columns and rows; otherwise rowcount."""
    log.info("Database admin: %s", statement.sql.strip()[:500])
    try:
        with engine.begin() as connection:
            result = connection.execute(text(statement.sql), statement.params)
            if result.returns_rows:
                columns = list(result.keys())
                rows = [[_plain(v) for v in row] for row in result]
                return {"columns": columns, "rows": rows, "rowcount": len(rows)}
            return {"columns": [], "rows": [], "rowcount": result.rowcount}
    except SQLAlchemyError as error:
        raise HTTPException(400, str(getattr(error, "orig", None) or error)) from None


@router.get("/file")
def download(settings: KeyDep) -> Response:
    """The whole database as one SQLite file (SQLite's backup API, so it's consistent)."""
    data = db_sync.snapshot(db_sync.database_file(settings))
    return Response(
        data,
        media_type="application/vnd.sqlite3",
        headers={"Content-Disposition": 'attachment; filename="wabot.db"'},
    )
