#!/usr/bin/env bash
# Start the dashboard locally on the simulation's data (simulated_month.db), with the dev login
# (pick a trainer, no password), at http://localhost:8000/dashboard. No tunnel, no WhatsApp.
# If the database doesn't exist yet, it's filled with the scenarios' correct answers (no OpenAI
# calls); run scripts/simulate_month.py yourself for the real model's data.
# Usage: bash run_dashboard.sh [extra uvicorn args]
#   PORT=8001 bash run_dashboard.sh      # another port
#   DB=wabot.db bash run_dashboard.sh    # another database, e.g. the real local one
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
DB="${DB:-simulated_month.db}"

if [[ ! -f "$DB" ]]; then
    if [[ "$DB" != simulated_month.db ]]; then
        echo "$DB doesn't exist." >&2
        exit 1
    fi
    echo "No $DB yet - filling it with the scenarios' correct answers (no OpenAI calls)."
    uv run python scripts/simulate_month.py --canned >/dev/null
fi

# An older database gets the new tables (its data stays).
DATABASE_URL="sqlite:///$DB" uv run alembic upgrade head

echo "Dashboard on $DB: http://localhost:$PORT/dashboard"
# Its own log file, so it can run next to run.sh (logs/wabot.log).
DATABASE_URL="sqlite:///$DB" DASHBOARD_DEV_LOGIN=true LOG_FILE="${LOG_FILE:-logs/dashboard.log}" \
    uv run uvicorn wabot.main:app --reload --reload-include "*.html" --reload-include "*.css" \
    --reload-include "*.js" --port "$PORT" "$@"
