#!/bin/sh
# Start the bot on Cloud Run (docs/DEPLOY.md): the database from the bucket (its last copy, or the
# seed on the very first start), the migrations, then uvicorn. After every request that changes
# the database, the app uploads it again (db_sync.py). At most one instance may run
# (--max-instances 1): two would each have their own copy.
set -eu

DB=/data/wabot.db
mkdir -p /data
python /app/deploy/fetch_db.py "$DB"

export DATABASE_URL="sqlite:///$DB"
alembic upgrade head

exec uvicorn wabot.main:app --host 0.0.0.0 --port "${PORT:-8080}" \
    --proxy-headers --forwarded-allow-ips='*'
