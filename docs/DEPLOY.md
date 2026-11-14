# Deploying to Google Cloud (Cloud Run)

Set up 2026-11-14. Project `yanek-bot`, region `europe-central2` (Warsaw).

## How it runs

```
Telegram ──POST /telegram/webhook──► Cloud Run service "yanek" (max 1 instance, scales to zero)
                                       start (deploy/start.sh):
                                         deploy/fetch_db.py  gs://yanek-bot-db/wabot.db → /data/wabot.db
                                                             (first start: seed/wabot.db; neither: empty)
                                         alembic upgrade head
                                         uvicorn
                                       every request: the reply runs inside it (REPLY_IN_REQUEST)
                                       after a request that changed the db: a copy → gs://yanek-bot-db/wabot.db
                                         (db_sync.py, before the response is sent)
```

- **Per-request billing, scale to zero:** Cloud Run gives the container CPU only while it handles a request, so nothing may run after the response. That's why the reply (transcription, LLM, saving, sending) runs before the webhook answers (`REPLY_IN_REQUEST=true`; Telegram waits), and why the database is uploaded before the response too. Cost: roughly $0–2 a month at this size. The alternative, an instance that's always on with CPU between requests (background replies, Litestream), costs ~$55–65 a month.
- **SQLite on Cloud Run:** the container's disk (`/data`, in memory) is gone when the instance stops, so the database lives in the bucket between starts. Only **one** instance may run (`--max-instances 1`): two would each have their own copy and overwrite each other's. During a deploy the old and new revision overlap for a few seconds; a message in exactly that moment can be lost. Fine for a few trainees; Cloud SQL Postgres (PLAN.md) when that stops being true.
- **Cold start:** after a quiet spell the first message waits for the container to start (a few seconds more).
- **Secrets** come from Secret Manager: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `DASHBOARD_SESSION_SECRET`. Other settings are environment variables on the service: `CHANNEL=telegram`, `REPLY_IN_REQUEST=true`, `DB_BUCKET=yanek-bot-db`, `LOG_FILE=` (logs go to Cloud Logging), `LOG_LEVEL=INFO`, `GOOGLE_CLIENT_ID` (not a secret). `--set-env-vars` replaces all of them, so the deploy command below lists every one. `DATABASE_URL` is set by `deploy/start.sh`.
- **Service account** `yanek-run@yanek-bot.iam.gserviceaccount.com`: object admin on the bucket, secret accessor on the three secrets.
- **The dashboard** is on the same service, `/dashboard`, with Google sign-in (set up 2026-11-14): the OAuth client "Yanek" in `yanek-bot` (Google Auth Platform → Clients; origin `https://yanek-1008452335758.europe-central2.run.app`, redirect URI `…/dashboard/login/google`; the app is in Testing, so each trainer's Google account must be added under Audience → Test users). A trainer signs in with the Google account in `trainers.email`. The client secret Google shows isn't used (the sign-in button only needs the client ID); keep it private. The dev login is refused on Cloud Run (`K_SERVICE`).

## Commands

gcloud is installed at `%LOCALAPPDATA%\Google\Cloud SDK` (on the PATH in new terminals); the project is set with `gcloud config set project yanek-bot`.

```
# Deploy the current code (builds the image with Cloud Build; .gcloudignore keeps .env and the databases out)
gcloud run deploy yanek --source . --region europe-central2 --project yanek-bot \
  --service-account yanek-run@yanek-bot.iam.gserviceaccount.com --allow-unauthenticated \
  --max-instances 1 --min-instances 0 --cpu 1 --memory 512Mi --timeout 300 --concurrency 20 \
  --set-env-vars "^@^CHANNEL=telegram@REPLY_IN_REQUEST=true@DB_BUCKET=yanek-bot-db@LOG_FILE=@LOG_LEVEL=INFO@GOOGLE_CLIENT_ID=1008452335758-2jj45lv2191lu7c50qmc1f4a73jduti7.apps.googleusercontent.com" \
  --set-secrets "TELEGRAM_BOT_TOKEN=TELEGRAM_BOT_TOKEN:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,DASHBOARD_SESSION_SECRET=DASHBOARD_SESSION_SECRET:latest"

# Point Telegram at the service (once; run.sh moves it back to a tunnel, see below)
uv run python scripts/set_webhook.py https://yanek-1008452335758.europe-central2.run.app

# Logs
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="yanek"' --limit 50 --freshness 1h --format="value(textPayload)"

# The cloud database, to look at it locally (a copy; changing it changes nothing in the cloud)
gcloud storage cp gs://yanek-bot-db/wabot.db cloud.db
DATABASE_URL=sqlite:///cloud.db bash run_dashboard.sh

# A new secret value (e.g. a new bot token), then redeploy or update the service so it reads :latest
printf '%s' '<value>' | gcloud secrets versions add TELEGRAM_BOT_TOKEN --data-file=-
```

**Replacing the cloud database** (e.g. with a prepared one): stop traffic first or it gets overwritten by the next change. Upload the file to `gs://yanek-bot-db/wabot.db`, then make the service restart (`gcloud run services update yanek --region europe-central2 --update-env-vars RESTARTED=<now>`), so it downloads it.

## Local development next to the cloud bot

There's one bot token, and Telegram sends to one webhook. `bash run.sh` registers its tunnel, which takes the bot away from Cloud Run until `set_webhook.py` points it back. To work locally without that: `TUNNEL=0 bash run.sh` (no Telegram) and the dashboard, or a second bot from @BotFather with its own token in `.env` for development.

The seed (`gs://yanek-bot-db/seed/wabot.db`, 2026-11-14) is the local `wabot.db` without the simulated trainers: Trener testowy and Paweł (Telegram connected), his messages and plan, no trainings.
