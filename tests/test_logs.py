import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wabot import logs
from wabot.config import Settings


@pytest.fixture
def restore_logging():
    """setup() changes global logging; put the test run's setup back afterwards."""
    yield
    logs.setup(Settings(log_file=""))


def test_the_log_file_rotates_and_keeps_only_a_few(tmp_path, monkeypatch, restore_logging):
    monkeypatch.setattr(logs, "MAX_BYTES", 2000)
    path = tmp_path / "logs" / "wabot.log"
    logs.setup(Settings(log_file=str(path)))
    for i in range(500):
        logging.getLogger("wabot.test").info("line %d %s", i, "x" * 50)
    files = sorted(p.name for p in path.parent.iterdir())
    assert files == ["wabot.log", "wabot.log.1", "wabot.log.2", "wabot.log.3"]
    assert all(p.stat().st_size <= 2000 for p in path.parent.iterdir())
    assert "line 499" in path.read_text(encoding="utf-8")


def test_the_level_decides_what_is_written(tmp_path, restore_logging):
    path = tmp_path / "wabot.log"
    logs.setup(Settings(log_level="WARNING", log_file=str(path)))
    logging.getLogger("wabot.test").info("an info line")
    logging.getLogger("wabot.test").warning("a warning")
    text = path.read_text(encoding="utf-8")
    assert "a warning" in text and "an info line" not in text


def test_debug_doesnt_turn_on_the_libraries_that_print_tokens(restore_logging):
    logs.setup(Settings(log_level="DEBUG", log_file=""))
    assert logging.getLogger("wabot").getEffectiveLevel() == logging.DEBUG
    # httpx's request lines have the URL, and Telegram's has the bot token.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_setup_twice_doesnt_double_the_lines(tmp_path, restore_logging):
    path = tmp_path / "wabot.log"
    logs.setup(Settings(log_file=str(path)))
    logs.setup(Settings(log_file=str(path)))
    logging.getLogger("wabot.test").warning("once")
    assert path.read_text(encoding="utf-8").count("once") == 1


def test_the_level_is_any_case():
    assert Settings(log_level="debug").log_level == "DEBUG"


def test_an_unknown_level_is_refused():
    with pytest.raises(ValueError):
        Settings(log_level="LOUD")


def test_the_app_logs_to_the_file_by_default(tmp_path):
    # A fresh interpreter in an empty folder: importing the app creates logs/wabot.log.
    env = {k: v for k, v in os.environ.items() if k not in ("LOG_FILE", "LOG_LEVEL")}
    code = "import wabot.main, logging; logging.getLogger('wabot.x').warning('hello file')"
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, check=True)
    assert "hello file" in Path(tmp_path, "logs", "wabot.log").read_text(encoding="utf-8")
