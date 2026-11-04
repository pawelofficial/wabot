import os
import tempfile
from pathlib import Path

import pytest

# Must be set before wabot modules are imported: the engine and settings are
# created at import time. Environment variables override .env.
_db_dir = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{Path(_db_dir) / 'test.db'}",
    WHATSAPP_TOKEN="test-token",
    WHATSAPP_PHONE_NUMBER_ID="123",
    WHATSAPP_APP_SECRET="test-secret",
    WHATSAPP_VERIFY_TOKEN="test-verify",
    OPENAI_API_KEY="test-openai-key",
    HARDCODED_FIRST_MESSAGE="false",
)

from fastapi.testclient import TestClient  # noqa: E402

from wabot import config  # noqa: E402

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
    Base.metadata.drop_all(engine)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
