# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Milestone 0 (developer setup) is done and so is milestone 1 (echo bot); milestone 2 (submissions: trainees, plans, weight and training in the database) is done; milestone 3 (trainer dashboard) is in progress, and milestone 4 (conversation: corrections, clarifications, diet) comes after it. The bot runs on Telegram since 2026-11-12 (Meta restricted the WhatsApp account); WhatsApp stays in the code, `CHANNEL` in `.env` switches. Since 2026-11-14 the bot also runs on Google Cloud: Cloud Run in project `yanek-bot` (`europe-central2`), SQLite kept in the bucket `gs://yanek-bot-db`, secrets in Secret Manager, scales to zero; how it works, the deploy command and the caveats (one instance only; `run.sh` moves the Telegram webhook to its tunnel) are in `docs/DEPLOY.md`. Settings and secrets (Telegram bot token, Meta access token, app secret, webhook verify token, OpenAI key) are in `.env` (gitignored; never print or commit it).

## Commands

```
uv sync                                  # install dependencies into .venv
uv run alembic upgrade head              # create/upgrade the local SQLite db (wabot.db)
uv run alembic revision --autogenerate --rev-id 0002 -m "add trainees"   # new migration; next free number, review the file
uv run alembic check                     # models and migrations agree
bash run.sh                              # migrate, messenger check, cloudflared tunnel + webhook (CHANNEL), bot on :8000
uv run python scripts/set_webhook.py <url>   # point the active channel's webhook at <url> (Telegram: /telegram/webhook, Meta: /webhook)
uv run uvicorn wabot.main:app --reload   # run the bot without migrating
uv run pytest                            # tests (single: uv run pytest tests/test_webhook.py::test_name)
uv run ruff check . && uv run ruff format .
uv run python scripts/add_trainee.py     # register WHATSAPP_TEST_RECIPIENT as a trainee (--help for options)
uv run python scripts/send_test.py       # check the messenger: Telegram prints the bot's link (and messages you once connected), WhatsApp sends a template
uv run python scripts/send_test_custom_first_message.py [--dry-run]   # bot-started message, composed by the app
uv run python scripts/simulate_month.py [--only kasia]  # scenarios/*.yaml through the real model into simulated_month.db (not wabot.db)
uv run python scripts/simulate_month.py --canned   # the same with the correct answers from the scenarios, no OpenAI
bash run_dashboard.sh                    # dashboard at :8001/dashboard on DATABASE_URL's database (migrated), dev login
uv run python scripts/setup_db.py [--simulated] [--new dev.db]   # create/migrate the database; --simulated adds the simulated month (backup first)
```

## Code layout

- `src/wabot/main.py` – FastAPI app: `POST /telegram/webhook` (secret header check, trainee lookup by chat; a new chat is asked for the phone number with a button, the shared own number links the chat to the registered trainee; a trainee's voice message is transcribed first and needs no "yanek"; store message, dedup by unique `external_id`, reply in a background task), `GET /webhook` (Meta's verification), `POST /webhook` (WhatsApp: signature check, trainee lookup by phone, the same storing and dedup; fixed reply for unregistered numbers), `reply()` (the trainee's waiting messages as a turn, LLM, save + mark them handled in one transaction, send back on the message's channel; a failed LLM call or save sends the apology and leaves them waiting)
- `src/wabot/conversation.py` – flow shared by the webhooks and scripts: the "yanek" name trigger, the trainee's chat history (both channels), the `Turn` a reply works on (`load_turn()`: messages still waiting – `messages.handled_at` null – are new, the handled ones before them context; `claim()` marks them handled in the save's transaction; `called_again()` skips a message the trainee followed up on), WhatsApp's 24 h window check, composing bot-started messages, `send_and_store()` (on the given channel, default `CHANNEL`)
- `src/wabot/telegram.py` – Telegram Bot API: parsing updates (a voice message's file ID and length), the webhook secret (derived from the bot token), sending (with the share-number keyboard), `download_file()`, `set_webhook()`, `bot_username()`; errors as `TelegramError` without the token (it's in every API URL; httpx's request logs are off for the same reason)
- `src/wabot/voice.py` – voice messages: `text_of()` downloads the recording from Telegram (`telegram.download_file()`) and has OpenAI transcribe it (`OPENAI_TRANSCRIBE_MODEL`, Polish); None (too long, `VOICE_MAX_SECONDS`, or failed) gets the fixed `VOICE_NOT_UNDERSTOOD`
- `src/wabot/trainees.py` – `find_by_phone()`, `find_by_telegram()`, `link_telegram()`, `normalize_phone()`, `trainer_named()`
- `src/wabot/entries.py` – `save()`: the extracted weight and training into `weight_entries` / `training_checkins`; every report is a new row and the newest per trainee per day is current, for trainings the newest of each kind (`outside_plan()`: a run besides the plan's training; another plan day on the same date is a correction) (`DAILY_UPDATES_ONLY=True`: one row per day and kind, a newer report updates it)
- `src/wabot/plan_history.py` – a trainee's plan versions (`trainee_plans`) and the trainer's plan library (`plan_templates`): every change of a trainee's plan goes through `assign()`, which adds a version and points `trainees.plan_version_id` to it (what the bot reads, `plans.of()`; `remove()` sets it to null); assigning a library plan copies it; each training check-in points to the version it was checked against
- `src/wabot/dashboard/` – the trainers' dashboard (Jinja templates, plain CSS/JS, no build step): `routes.py` pages under `/dashboard`, `trainee_pages.py` adding a trainee (Dodaj podopiecznego: name, phone, consent date, optionally a library plan; then the bot's link), `plan_pages.py` the trainee's plan (history, assigning from the library, the editor) and the plan library (`/dashboard/plans`), `auth.py` signed session cookie plus `current_trainer` / `trainee_of_trainer` (404 for another trainer's trainee; every page uses them; `template_of_trainer` / `version_of_trainee` the same for library plans and plan versions), `queries.py` the reads (`current_per_day()`: the newest row per day; `workouts()`: a day's current trainings, the newest of each kind: the plan's and one outside the plan, `training_checkins.outside_plan`; `conversations()`: the messages each report was saved from and Yanek's answer, shown by `templates/_conversations.html` as "Pokaż wiadomości"), `training_pages.py` Treningi (the month calendar and the month's trainings, `?month=2026-10`), `templates/base.html` the sidebar with the trainee dropdown, `static/weight_chart.js` the weight chart (SVG, no chart library), `plan_editor.py` + `templates/plan_edit.html` + `static/plan_editor.js` the plan editor (the form posts the plan as JSON; `plan_editor.parse()` checks it with `plans.TrainingPlan` and says the errors in Polish by the error types `plans.py` raises). Sign-in: Google (`GOOGLE_CLIENT_ID`; the ID token's verified email must be a `trainers.email`), or `DASHBOARD_DEV_LOGIN=True`, which lists trainers to sign in as (local only)
- `src/wabot/db_sync.py` – Cloud Run only (`DB_BUCKET`): after a request that changed the SQLite database, `main.py`'s middleware uploads a consistent copy to the bucket before the response; `deploy/start.sh` + `deploy/fetch_db.py` download it at start (the seed on the first start). With `REPLY_IN_REQUEST` the webhooks run the reply before answering (`main.after_storing()`), since Cloud Run gives CPU only during a request
- `Dockerfile`, `deploy/`, `.gcloudignore` – the Cloud Run image (`docs/DEPLOY.md`); `.gcloudignore` / `.dockerignore` keep `.env` and the databases out of it
- `src/wabot/logs.py` – logging setup, called by `main.py`: `LOG_LEVEL` (DEBUG adds message texts, the model's prompt and raw answer), the console and a rotating `LOG_FILE` (default `logs/wabot.log`, 5 MB, 3 backups) that also gets uvicorn's lines; httpx/httpcore stay at WARNING at any level (their lines have the Telegram token)
- `src/wabot/utils.py` – `hardcoded_first_message()`, used instead of the LLM when `HARDCODED_FIRST_MESSAGE=True`
- `src/wabot/whatsapp.py` – Meta Cloud API: signature check (refuses all without an app secret), webhook parsing (incl. failed delivery statuses), sending
- `src/wabot/models.py` – SQLAlchemy models (`Trainer`, `Trainee`, `Message`, `WeightEntry`, `TrainingCheckin`, `TrainingExercise`, `PlanTemplate`, `TraineePlan`); use `UTCDateTime` for every timestamp
- `src/wabot/db.py` – engine and sessions (SQLite runs with WAL and a busy timeout)
- `src/wabot/assistant.py` – LLM calls (OpenAI Responses API, `OPENAI_MODEL`): `generate_reply(settings, trainee, context, new, now)` returns an `LlmOutput` (reply text + extracted `weight` / `training`), `generate_first_message()`; `build_instructions()` injects the trainee's name and training plan; dates are in the trainee's timezone; output that fails validation is retried once with the errors
- `src/wabot/prompts.yaml` – the LLM prompts: `system`, `trainee_name` (`{trainee_name}` is `trainees.name`, so the bot uses the right grammatical forms), `training_plan` (`{training_plan}` is replaced with `plans.TrainingPlan.prompt_text()`), `no_training_plan`, `new_messages_marker` (inserted before the first message still waiting, `HISTORY_MODE`), `first_message_task`; loaded at startup. Keep the field rules in sync with `llm_output.py`
- `src/wabot/llm_output.py` – pydantic models of the LLM output (the JSON schema sent to OpenAI is generated from them) and `validate_output()`: structure, plausible values, dates, plan days and exercise IDs that must be in the plan; IDs of exercises outside the plan come from the catalog by name
- `src/wabot/exercises.yaml` + `exercises.py` – exercise catalog (fixed IDs, Polish names, aliases, muscle group); its IDs are the allowed values of `exercise_id` in the schema and of plan exercises; `lookup()` matches a name or alias. Not sent to the LLM as a whole. IDs are permanent once data uses them – add, don't rename
- `src/wabot/plans.py` – training plans (every stored day has an ID that stays through renames, `with_day_ids()`; check-ins point to their day by it; and a label for the calendar, `labels()`: the trainer's or A, B, C by position): pydantic models (days of catalog exercise IDs with sets/reps/weight and the trainer's allowed replacements, plus notes), `load()` a YAML file, `of(trainee)` from the db (JSON), `prompt_text()` for the LLM
- `plans/example.yaml` – example training plan; `scripts/add_trainee.py` gives it to new trainees. `upper_lower.yaml` (fixed weekdays) and `beginner_fbw.yaml` (alternating workouts) are two more
- `scenarios/*.yaml` + `src/wabot/simulation.py` – a month of reports per simulated trainee (messages, the correct model answer, what must be saved), replayed through `main.reply()` at simulated times by `tests/test_month_scenario.py` (canned answers) and `scripts/simulate_month.py` (real model)
- `scripts/add_trainee.py` – registers or updates a trainee and their trainer (`--trainer-email` for dashboard sign-in) (the dashboard adds trainees too, `trainee_pages.py`); plans can also be edited in the dashboard
- `alembic/` – migrations; `UTCDateTime` is rendered as plain `DateTime(timezone=True)`

`DATABASE_URL` (`.env`, `sqlite:///wabot.db`) is the one database everything uses: the bot (it logs it at startup), the dashboard, alembic and the scripts; only `scripts/simulate_month.py` writes its own scratch `simulated_month.db`, which it recreates.

The dashboard is developed on the simulation's data (`scripts/setup_db.py --simulated` adds it to the database, built by the same migrations), so the simulated trainers and trainees must have every field real ones have: when a change adds a column or a field the dashboard needs, give it a value in `scenarios/*.yaml` / `simulation.register()` in the same change (`test_simulated_trainers_and_trainees_have_every_field_filled` fails otherwise).

`docs/CONVERSATION.md` is the design of the conversation handling: the cases (a report, several messages, corrections, clarifying questions and their answers, ...), the planned `ConversationHandler` class and the fallbacks; a draft for review, so check it before changing how replies work, and keep it in sync.

`docs/CODE_EXPLANATION.md` walks through the runtime flows (which function calls which, where data ends up). When a change adds, removes or reorders a step in one of those flows, update it in the same change.

The product plan, decisions and milestones are in `docs/PLAN.md`; read it before doing anything, and keep it updated when decisions change. Don't start a new milestone until the user asks.

## Key decisions (details in docs/PLAN.md)

- Python, FastAPI on Cloud Run, Cloud SQL Postgres, Cloud Storage, Cloud Tasks, Cloud Scheduler; region `europe-central2`.
- Messenger: Telegram Bot API now (`CHANNEL=telegram`), Meta WhatsApp Cloud API kept for later (`CHANNEL=whatsapp`), both called directly. Replies go back on the channel the message came from. On WhatsApp, reminders outside the 24 h window need approved templates.
- Multi-trainer: every trainee-owned row carries `trainer_id`; the trainee is resolved server-side from the sender's phone number, and the LLM's tools never accept a trainee ID from the model.
- The bot answers only messages that mention it by name ("yanek"); everything else is stored as history.
- Bot language is Polish. Trainee plans are built from the exercise catalog (IDs plus the replacements the trainer allows); only the plan's exercises go into the prompt.
- Trainers register their trainees in the dashboard (consent is collected on paper); unknown numbers get a fixed reply.
- Photos are stored and shown on the dashboard, never sent to the LLM.
- Planned tooling: `uv`, `ruff`, `pytest`.
