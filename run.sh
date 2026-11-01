#!/usr/bin/env bash
# Start the bot locally: apply pending migrations, then run uvicorn with auto-reload.
# Usage: bash run.sh [extra uvicorn args, e.g. --port 8001]
set -euo pipefail
cd "$(dirname "$0")"

uv run alembic upgrade head

# Startup check: sends a template message to the test recipient, so a
# WhatsApp message on your phone means the token and number work.
if ! uv run python scripts/send_test.py; then
    echo "WARNING: test message failed - check WHATSAPP_TOKEN in .env (it expires after ~24 h)." >&2
fi

exec uv run uvicorn wabot.main:app --reload "$@"
