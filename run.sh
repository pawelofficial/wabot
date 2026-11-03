#!/usr/bin/env bash
# Start the bot locally: apply pending migrations, send a test message, start a cloudflared
# tunnel and point Meta's webhook at it, then run uvicorn with auto-reload. Ctrl+C stops both.
# Usage: bash run.sh [extra uvicorn args]
#   PORT=8001 bash run.sh    # another port
#   TUNNEL=0 bash run.sh     # no tunnel, keep Meta's webhook as it is
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
CLOUDFLARED="${CLOUDFLARED:-$(command -v cloudflared || echo "$HOME/tools/cloudflared.exe")}"
TUNNEL_LOG=.cloudflared.log

uv run alembic upgrade head

# Startup check: sends a template message to the test recipient, so a
# WhatsApp message on your phone means the token and number work.
if ! uv run python scripts/send_test.py; then
    echo "WARNING: test message failed - check WHATSAPP_TOKEN in .env (it expires after ~24 h)." >&2
fi

if [[ "${TUNNEL:-1}" != 0 ]]; then
    if [[ -x "$CLOUDFLARED" ]]; then
        "$CLOUDFLARED" tunnel --no-autoupdate --url "http://localhost:$PORT" >"$TUNNEL_LOG" 2>&1 &
        tunnel_pid=$!
        trap 'kill "$tunnel_pid" 2>/dev/null' EXIT

        # In the background: wait for the tunnel's address, then register it with Meta once the
        # bot (started below) answers through it.
        (
            for _ in $(seq 30); do
                url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)
                [[ -n "$url" ]] && break
                sleep 1
            done
            if [[ -z "$url" ]]; then
                echo "WARNING: cloudflared printed no URL - see $TUNNEL_LOG" >&2
            elif ! uv run python scripts/set_webhook.py "$url"; then
                echo "WARNING: set the Callback URL by hand: $url/webhook" >&2
            fi
        ) &
    else
        echo "WARNING: cloudflared not found (set CLOUDFLARED=path/to/cloudflared) - no tunnel." >&2
    fi
fi

uv run uvicorn wabot.main:app --reload --reload-include "*.yaml" --port "$PORT" "$@"
