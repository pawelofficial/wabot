# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Milestone 0 (developer setup) is done and so is milestone 1 (echo bot); development is local until the production milestone. Settings and secrets (Meta access token, app secret, webhook verify token) are in `.env` (gitignored; never print or commit it).

## Commands

```
uv sync                                  # install dependencies into .venv
uv run alembic upgrade head              # create/upgrade the local SQLite db (wabot.db)
uv run alembic revision --autogenerate --rev-id 0002 -m "add trainees"   # new migration; next free number, review the file
uv run alembic check                     # models and migrations agree
bash run.sh                              # migrate, then run the bot on http://localhost:8000
uv run uvicorn wabot.main:app --reload   # run the bot without migrating
uv run pytest                            # tests (single: uv run pytest tests/test_webhook.py::test_name)
uv run ruff check . && uv run ruff format .
uv run python scripts/send_test.py       # send a test template message
```

## Code layout

- `src/wabot/main.py` – FastAPI app: `GET /webhook` (Meta's verification), `POST /webhook` (signature check, store message, dedup by unique `wamid`, reply in a background task)
- `src/wabot/whatsapp.py` – Cloud API: signature check, webhook parsing, sending
- `src/wabot/models.py` – SQLAlchemy models; use `UTCDateTime` for every timestamp
- `src/wabot/db.py` – engine and sessions (SQLite runs with WAL and a busy timeout)
- `src/wabot/assistant.py` – Claude call and system prompt (Polish replies from recent chat history)
- `alembic/` – migrations; `UTCDateTime` is rendered as plain `DateTime(timezone=True)`

The product plan, decisions and milestones are in `docs/PLAN.md`; read it before doing anything, and keep it updated when decisions change. Don't start a new milestone until the user asks.

## Key decisions (details in docs/PLAN.md)

- Python, FastAPI on Cloud Run, Cloud SQL Postgres, Cloud Storage, Cloud Tasks, Cloud Scheduler; region `europe-central2`.
- Meta WhatsApp Cloud API directly. Reminders outside the 24 h window need approved templates.
- Multi-trainer: every trainee-owned row carries `trainer_id`; the trainee is resolved server-side from the sender's phone number, and Claude's tools never accept a trainee ID from the model.
- Bot language is Polish. Trainee plans are free text written by the trainer.
- Trainers register their trainees in the dashboard (consent is collected on paper); unknown numbers get a fixed reply.
- Photos are stored and shown on the dashboard, never sent to Claude.
- Planned tooling: `uv`, `ruff`, `pytest`.
