#!/usr/bin/env bash
# Start the bot locally: apply pending migrations, check the messenger (CHANNEL in .env:
# telegram or whatsapp), start a cloudflared tunnel and point the messenger's webhook at it,
# then run uvicorn with auto-reload. Ctrl+C stops both.
# Usage: bash run.sh [extra uvicorn args]
#   PORT=8001 bash run.sh    # another port
#   TUNNEL=0 bash run.sh     # no tunnel, keep the webhook as it is
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
CLOUDFLARED="${CLOUDFLARED:-$(command -v cloudflared || echo "$HOME/tools/cloudflared.exe")}"
TUNNEL_LOG=.cloudflared.log

uv run alembic upgrade head

# Startup check: Telegram - the bot's token works (prints the bot's link, and messages you if
# you've connected); WhatsApp - a template message to the test recipient.
if ! uv run python scripts/send_test.py; then
    echo "WARNING: the messenger check failed - see above (the token in .env?)." >&2
fi

if [[ "${TUNNEL:-1}" != 0 ]]; then
    if [[ -x "$CLOUDFLARED" ]]; then
        "$CLOUDFLARED" tunnel --no-autoupdate --url "http://localhost:$PORT" >"$TUNNEL_LOG" 2>&1 &
        tunnel_pid=$!
        trap 'kill "$tunnel_pid" 2>/dev/null' EXIT

        # In the background: wait for the tunnel's address, then register it with Telegram or
        # Meta once the bot (started below) answers through it.
        (
            for _ in $(seq 30); do
                url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)
                [[ -n "$url" ]] && break
                sleep 1
            done
            if [[ -z "$url" ]]; then
                echo "WARNING: cloudflared printed no URL - see $TUNNEL_LOG" >&2
            elif ! uv run python scripts/set_webhook.py "$url"; then
                echo "WARNING: the webhook isn't set - see above (tunnel: $url)" >&2
            fi
        ) &
    else
        echo "WARNING: cloudflared not found (set CLOUDFLARED=path/to/cloudflared) - no tunnel." >&2
    fi
fi

uv run uvicorn wabot.main:app --reload --reload-include "*.yaml" --port "$PORT" "$@"
