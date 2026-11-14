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

# Windows lets a second server start on a port that's already taken, and the old one keeps
# answering (with old code). Refuse instead: something already answers on $PORT.
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    echo "Port $PORT is already in use - an old bot or dashboard is still running." >&2
    echo "Stop it (Ctrl+C in its window, or: netstat -ano | grep :$PORT, then taskkill //PID <pid> //T //F)," >&2
    echo "or use another port: PORT=8002 bash $(basename "$0")" >&2
    exit 1
fi
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
        # Not started from here directly: on Windows every auto-reload of uvicorn sends Ctrl+C to
        # the whole console, which stopped the tunnel too (scripts/start_tunnel.py).
        if command -v cygpath >/dev/null; then
            CLOUDFLARED=$(cygpath -w "$CLOUDFLARED")
        fi
        tunnel_pid=$(uv run python scripts/start_tunnel.py "$CLOUDFLARED" "$PORT" "$TUNNEL_LOG")
        tunnel_pid=${tunnel_pid//$'\r'/}
        if command -v taskkill >/dev/null; then
            trap 'taskkill //PID "$tunnel_pid" //F >/dev/null 2>&1' EXIT
        else
            trap 'kill "$tunnel_pid" 2>/dev/null' EXIT
        fi

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

# Reloads watch only src/ (the code and prompts.yaml): a reload drops the requests in flight, and
# changes elsewhere (.venv, logs, the databases) shouldn't cause one.
uv run uvicorn wabot.main:app --reload --reload-dir src --reload-include "*.yaml" --port "$PORT" "$@"
