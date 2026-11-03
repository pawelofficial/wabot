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
bash run.sh                              # migrate, test message, cloudflared tunnel + Meta webhook, bot on :8000
uv run python scripts/set_webhook.py <url>   # point Meta's webhook at <url>/webhook
uv run uvicorn wabot.main:app --reload   # run the bot without migrating
uv run pytest                            # tests (single: uv run pytest tests/test_webhook.py::test_name)
uv run ruff check . && uv run ruff format .
uv run python scripts/send_test.py       # send a test template message
uv run python scripts/send_test_custom_first_message.py [--dry-run]   # bot-started message, composed by the app
```

## Code layout

- `src/wabot/main.py` – FastAPI app: `GET /webhook` (Meta's verification), `POST /webhook` (signature check, store message, dedup by unique `wamid`, reply in a background task)
- `src/wabot/conversation.py` – flow shared by the webhook and scripts: the "yanek" name trigger, chat history, training plan lookup, 24 h window check, composing bot-started messages, send-and-store
- `src/wabot/utils.py` – `hardcoded_first_message()`, used instead of the LLM when `HARDCODED_FIRST_MESSAGE=True`
- `src/wabot/whatsapp.py` – Cloud API: signature check, webhook parsing (incl. failed delivery statuses), sending
- `src/wabot/models.py` – SQLAlchemy models; use `UTCDateTime` for every timestamp
- `src/wabot/db.py` – engine and sessions (SQLite runs with WAL and a busy timeout)
- `src/wabot/assistant.py` – LLM calls (OpenAI Responses API, `OPENAI_MODEL`): `generate_reply()` returns an `LlmOutput` (reply text + extracted `weight` / `training`), `generate_first_message()`; `build_instructions()` injects the training plan; output that fails validation is retried once with the errors
- `src/wabot/prompts.yaml` – the LLM prompts: `system`, `training_plan` (`{training_plan}` is replaced with the plan text), `no_training_plan`, `new_messages_marker` (inserted after the bot's last message, `HISTORY_MODE`), `first_message_task`; loaded at startup. Keep the field rules in sync with `llm_output.py`
- `src/wabot/llm_output.py` – pydantic models of the LLM output (the JSON schema sent to OpenAI is generated from them) and `validate_output()`: structure, plausible values, dates, names that must be in the plan
- `src/wabot/exercises.yaml` + `exercises.py` – exercise catalog (fixed IDs, Polish names, aliases, muscle group); injected into the prompt, and its IDs are the allowed values of `exercise_id` in the schema. IDs are permanent once data uses them – add, don't rename
- `plans/example.txt` – example training plan (free text, as a trainer writes it), injected for every trainee until plans are in the db
- `alembic/` – migrations; `UTCDateTime` is rendered as plain `DateTime(timezone=True)`

`docs/CODE_EXPLANATION.md` walks through the runtime flows (which function calls which, where data ends up). When a change adds, removes or reorders a step in one of those flows, update it in the same change.

The product plan, decisions and milestones are in `docs/PLAN.md`; read it before doing anything, and keep it updated when decisions change. Don't start a new milestone until the user asks.

## Key decisions (details in docs/PLAN.md)

- Python, FastAPI on Cloud Run, Cloud SQL Postgres, Cloud Storage, Cloud Tasks, Cloud Scheduler; region `europe-central2`.
- Meta WhatsApp Cloud API directly. Reminders outside the 24 h window need approved templates.
- Multi-trainer: every trainee-owned row carries `trainer_id`; the trainee is resolved server-side from the sender's phone number, and the LLM's tools never accept a trainee ID from the model.
- The bot answers only messages that mention it by name ("yanek"); everything else is stored as history.
- Bot language is Polish. Trainee plans are free text written by the trainer.
- Trainers register their trainees in the dashboard (consent is collected on paper); unknown numbers get a fixed reply.
- Photos are stored and shown on the dashboard, never sent to the LLM.
- Planned tooling: `uv`, `ruff`, `pytest`.
