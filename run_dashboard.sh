#!/usr/bin/env bash
# Start the dashboard locally with the dev login (pick a trainer, no password), at
# http://localhost:8001/dashboard, on the database DATABASE_URL points to (.env; the same one the
# bot uses, default wabot.db). Port 8001, so it runs next to run.sh's bot on 8000. No tunnel,
# no messenger. An empty database: scripts/setup_db.py (--simulated adds the simulated month).
# Usage: bash run_dashboard.sh [extra uvicorn args]
#   PORT=8002 bash run_dashboard.sh                           # another port
#   DATABASE_URL=sqlite:///dev.db bash run_dashboard.sh       # another database, just this once
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8001}"

# An older database gets the new tables (its data stays).
uv run alembic upgrade head

echo "Dashboard: http://localhost:$PORT/dashboard"
# Its own log file, so it can run next to run.sh (logs/wabot.log). The bot logs the database it
# uses at startup.
DASHBOARD_DEV_LOGIN=true LOG_FILE="${LOG_FILE:-logs/dashboard.log}" \
    uv run uvicorn wabot.main:app --reload --reload-dir src --reload-include "*.html" \
    --reload-include "*.css" --reload-include "*.js" --reload-include "*.yaml" --port "$PORT" "$@"
