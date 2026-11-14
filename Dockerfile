# The bot and the dashboard on Cloud Run (docs/DEPLOY.md). SQLite on the container's disk, kept
# in a Cloud Storage bucket between starts (deploy/start.sh downloads it, db_sync.py uploads it).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first, so a code change doesn't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
# A checkout on Windows may have given the script CRLF line endings, which /bin/sh can't run.
RUN uv sync --frozen --no-dev \
    && sed -i 's/\r$//' deploy/start.sh \
    && chmod +x deploy/start.sh

CMD ["/app/deploy/start.sh"]
