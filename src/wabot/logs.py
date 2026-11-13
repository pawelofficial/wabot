"""Logging setup: to the console and to a file that rotates (LOG_LEVEL and LOG_FILE in .env).

The file keeps the last few megabytes: when wabot.log reaches MAX_BYTES it becomes wabot.log.1
(the older ones move up to .2, .3, ...) and the oldest beyond BACKUPS is deleted, so the logs
never take more than about MAX_BYTES * (BACKUPS + 1) of disk.

DEBUG adds what helps to find out why the bot answered as it did: the text of each message,
what the model got (the instructions with the trainee's name and plan) and what it answered.
That's trainees' data, so the file stays local (*.log is gitignored).
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from wabot.config import Settings

MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Libraries whose INFO/DEBUG lines are noise here, or worse: httpx's print the request URL,
# which for Telegram has the bot token in it. They log only warnings and errors at any level.
QUIET = ("httpx", "httpcore", "openai", "multipart", "python_multipart", "asyncio")

# uvicorn's own loggers (the "GET /..." access lines, startup) don't reach the root logger;
# the file gets them too, so it has the whole story.
UVICORN = ("uvicorn", "uvicorn.access")


def setup(settings: Settings) -> None:
    """Set the level and the handlers. Called again (tests, reloads) it replaces its handlers."""
    level = logging.getLevelName(settings.log_level)
    root = logging.getLogger()
    root.setLevel(level)
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)

    formatter = logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    for logger in (root, *map(logging.getLogger, UVICORN)):
        for handler in [h for h in logger.handlers if getattr(h, "wabot", False)]:
            logger.removeHandler(handler)
            handler.close()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    _add(root, console)

    if settings.log_file:
        path = Path(settings.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        file.setFormatter(formatter)
        _add(root, file)
        for name in UVICORN:
            _add(logging.getLogger(name), file)


def _add(logger: logging.Logger, handler: logging.Handler) -> None:
    handler.wabot = True  # marks the handlers setup() owns
    logger.addHandler(handler)
