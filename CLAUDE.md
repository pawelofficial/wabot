# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Milestone 0 (developer setup) is done and so is milestone 1 (echo bot); milestone 2 (submissions) is in progress – trainees, plans, weight and training are in the database, diet isn't yet. Development is local until the production milestone. Settings and secrets (Meta access token, app secret, webhook verify token) are in `.env` (gitignored; never print or commit it).

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
uv run python scripts/add_trainee.py     # register WHATSAPP_TEST_RECIPIENT as a trainee (--help for options)
uv run python scripts/send_test.py       # send a test template message
uv run python scripts/send_test_custom_first_message.py [--dry-run]   # bot-started message, composed by the app
uv run python scripts/simulate_month.py [--only kasia]  # scenarios/*.yaml through the real model into simulated_month.db (not wabot.db)
```

## Code layout

- `src/wabot/main.py` – FastAPI app: `GET /webhook` (Meta's verification), `POST /webhook` (signature check, trainee lookup by phone, store message, dedup by unique `wamid`, reply in a background task; fixed reply for unregistered numbers), `reply()` (LLM, save, send)
- `src/wabot/conversation.py` – flow shared by the webhook and scripts: the "yanek" name trigger, the trainee's chat history, `asked_again()` (skip a message the trainee followed up on), 24 h window check, composing bot-started messages, send-and-store
- `src/wabot/trainees.py` – `find_by_phone()`, `normalize_phone()`, `trainer_named()`
- `src/wabot/entries.py` – `save()`: the extracted weight and training into `weight_entries` / `training_checkins`; every report is a new row and the newest per trainee per day is current (`DAILY_UPDATES_ONLY=True`: one row per day, a newer report updates it)
- `src/wabot/utils.py` – `hardcoded_first_message()`, used instead of the LLM when `HARDCODED_FIRST_MESSAGE=True`
- `src/wabot/whatsapp.py` – Cloud API: signature check, webhook parsing (incl. failed delivery statuses), sending
- `src/wabot/models.py` – SQLAlchemy models (`Trainer`, `Trainee`, `Message`, `WeightEntry`, `TrainingCheckin`, `TrainingExercise`); use `UTCDateTime` for every timestamp
- `src/wabot/db.py` – engine and sessions (SQLite runs with WAL and a busy timeout)
- `src/wabot/assistant.py` – LLM calls (OpenAI Responses API, `OPENAI_MODEL`): `generate_reply(settings, trainee, history, now)` returns an `LlmOutput` (reply text + extracted `weight` / `training`), `generate_first_message()`; `build_instructions()` injects the trainee's name and training plan; dates are in the trainee's timezone; output that fails validation is retried once with the errors
- `src/wabot/prompts.yaml` – the LLM prompts: `system`, `trainee_name` (`{trainee_name}` is `trainees.name`, so the bot uses the right grammatical forms), `training_plan` (`{training_plan}` is replaced with `plans.TrainingPlan.prompt_text()`), `no_training_plan`, `new_messages_marker` (inserted after the bot's last message, `HISTORY_MODE`), `first_message_task`; loaded at startup. Keep the field rules in sync with `llm_output.py`
- `src/wabot/llm_output.py` – pydantic models of the LLM output (the JSON schema sent to OpenAI is generated from them) and `validate_output()`: structure, plausible values, dates, plan days and exercise IDs that must be in the plan; IDs of exercises outside the plan come from the catalog by name
- `src/wabot/exercises.yaml` + `exercises.py` – exercise catalog (fixed IDs, Polish names, aliases, muscle group); its IDs are the allowed values of `exercise_id` in the schema and of plan exercises; `lookup()` matches a name or alias. Not sent to the LLM as a whole. IDs are permanent once data uses them – add, don't rename
- `src/wabot/plans.py` – training plans: pydantic models (days of catalog exercise IDs with sets/reps/weight and the trainer's allowed replacements, plus notes), `load()` a YAML file, `of(trainee)` from the db (JSON), `prompt_text()` for the LLM
- `plans/example.yaml` – example training plan; `scripts/add_trainee.py` gives it to new trainees. `upper_lower.yaml` (fixed weekdays) and `beginner_fbw.yaml` (alternating workouts) are two more
- `scenarios/*.yaml` + `src/wabot/simulation.py` – a month of reports per simulated trainee (messages, the correct model answer, what must be saved), replayed through `main.reply()` at simulated times by `tests/test_month_scenario.py` (canned answers) and `scripts/simulate_month.py` (real model)
- `scripts/add_trainee.py` – registers or updates a trainee and their trainer until the dashboard exists
- `alembic/` – migrations; `UTCDateTime` is rendered as plain `DateTime(timezone=True)`

`docs/CODE_EXPLANATION.md` walks through the runtime flows (which function calls which, where data ends up). When a change adds, removes or reorders a step in one of those flows, update it in the same change.

The product plan, decisions and milestones are in `docs/PLAN.md`; read it before doing anything, and keep it updated when decisions change. Don't start a new milestone until the user asks.

## Key decisions (details in docs/PLAN.md)

- Python, FastAPI on Cloud Run, Cloud SQL Postgres, Cloud Storage, Cloud Tasks, Cloud Scheduler; region `europe-central2`.
- Meta WhatsApp Cloud API directly. Reminders outside the 24 h window need approved templates.
- Multi-trainer: every trainee-owned row carries `trainer_id`; the trainee is resolved server-side from the sender's phone number, and the LLM's tools never accept a trainee ID from the model.
- The bot answers only messages that mention it by name ("yanek"); everything else is stored as history.
- Bot language is Polish. Trainee plans are built from the exercise catalog (IDs plus the replacements the trainer allows); only the plan's exercises go into the prompt.
- Trainers register their trainees in the dashboard (consent is collected on paper); unknown numbers get a fixed reply.
- Photos are stored and shown on the dashboard, never sent to the LLM.
- Planned tooling: `uv`, `ruff`, `pytest`.
