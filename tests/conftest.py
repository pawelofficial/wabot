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
    ANTHROPIC_API_KEY="test-anthropic-key",
)

from fastapi.testclient import TestClient  # noqa: E402

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
