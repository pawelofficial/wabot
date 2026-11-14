import os
import tempfile
from pathlib import Path

import pytest

# Must be set before wabot modules are imported: the engine and settings are
# created at import time. Environment variables override .env.
_db_dir = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{Path(_db_dir) / 'test.db'}",
    TELEGRAM_BOT_TOKEN="123456:test-telegram-token",
    WHATSAPP_TOKEN="test-token",
    WHATSAPP_PHONE_NUMBER_ID="123",
    WHATSAPP_APP_SECRET="test-secret",
    WHATSAPP_VERIFY_TOKEN="test-verify",
    OPENAI_API_KEY="test-openai-key",
    HARDCODED_FIRST_MESSAGE="false",
    LOG_FILE="",  # no logs/wabot.log from tests
)

from fastapi.testclient import TestClient  # noqa: E402

from wabot import config, telegram  # noqa: E402

# Tests don't read .env: what they run with mustn't depend on the developer's local settings
# (HISTORY_MODE, DAILY_UPDATES_ONLY, GOOGLE_CLIENT_ID, ...). Only the values above and the code's
# defaults apply.
for _settings_class in (config.DatabaseSettings, config.Settings):
    _settings_class.model_config["env_file"] = None

from wabot.db import engine  # noqa: E402
from wabot.main import app  # noqa: E402
from wabot.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.create_all(engine)
    yield
    # trainees and trainee_plans point at each other (the current plan version), so with
    # foreign keys on, SQLite can drop neither while they have rows.
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        Base.metadata.drop_all(connection)
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


REAL_TELEGRAM_CALL = telegram._call


@pytest.fixture(autouse=True)
def no_telegram(monkeypatch):
    """No test reaches Telegram: tests that send replace telegram.send_text (tests/
    test_telegram.py's `telegram_sent`) or test telegram._call with httpx faked."""

    def refuse(settings, method, payload=None):
        raise AssertionError(f"a test called Telegram's {method}")

    monkeypatch.setattr(telegram, "_call", refuse)
