# How the code works

A walk through what happens at runtime: which function calls which, in what order, and where data ends up. It describes flows, not every function. For *why* things are built this way, see [PLAN.md](PLAN.md).

**Keep this file in sync with the code.** When a change adds, removes or reorders a step in one of the flows below, update the flow in the same change.

## The files in one line each

| File | Role |
|---|---|
| `src/wabot/main.py` | FastAPI app: the HTTP endpoints Meta calls, `reply()` (the background job that answers a trainee), `reply_unregistered()` |
| `src/wabot/whatsapp.py` | Talking to Meta: check the webhook signature, parse the payload, send a text |
| `src/wabot/trainees.py` | Who is behind a phone number (`find_by_phone()`), normalizing numbers, finding or creating a trainer |
| `src/wabot/conversation.py` | The glue: "is the bot mentioned?", chat history from the db, "did the trainee ask again?", send + store |
| `src/wabot/assistant.py` | Talking to OpenAI: build the prompt, call the model, retry on invalid output |
| `src/wabot/llm_output.py` | The shape of the model's answer (pydantic models → JSON schema) and `validate_output()` |
| `src/wabot/entries.py` | Saving the extracted weight and training into the db (a new row per report; `DAILY_UPDATES_ONLY` updates the day's row instead) |
| `src/wabot/prompts.yaml` | The Polish prompt texts |
| `src/wabot/exercises.yaml` / `exercises.py` | The exercise catalog: fixed IDs, names, aliases; `lookup()` finds an ID by name |
| `src/wabot/plans.py` | Training plans: days of catalog exercises with the trainer's replacements; loading, checking, the text the LLM sees |
| `src/wabot/models.py` | Database tables (see the end of this file) |
| `src/wabot/db.py` | Database engine and sessions |
| `src/wabot/config.py` | `Settings`: every value from `.env` |
| `src/wabot/utils.py` | `hardcoded_first_message()`, a fixed text used instead of the LLM |
| `plans/example.yaml` | Example training plan; `scripts/add_trainee.py` gives it to new trainees |
| `plans/upper_lower.yaml`, `plans/beginner_fbw.yaml` | Two more real plans: an upper/lower split on fixed weekdays, and two alternating full-body workouts for a beginner |
| `scenarios/*.yaml` | A month of reports per simulated trainee (Kasia, Tomek, Ola: one plan each, two trainers): the messages, the answer a correct model gives, and what must be saved. The format is explained at the top of `kasia.yaml` |
| `src/wabot/simulation.py` | Replays the scenarios through `main.reply()` at simulated times (used by the month test and `scripts/simulate_month.py`, not by the app) |
| `run.sh` | Local start: migrate, test message, tunnel, webhook, uvicorn |
| `scripts/` | One-off command-line tools (see Flows 3 and 5) |

## What happens at import / startup

Some files do work the moment they're imported, once per process:

- `config.py`: nothing yet; `get_settings()` reads `.env` when it's first called and caches the result.
- `db.py`: creates the SQLAlchemy `engine` from `DATABASE_URL` (default `sqlite:///wabot.db`) and the `SessionLocal` session factory. For SQLite it turns on WAL, a 5 s busy timeout and foreign keys.
- `exercises.py`: reads `exercises.yaml` into `CATALOG` (a list of `CatalogExercise`), `IDS`, `BY_ID` and a name/alias → ID table for `lookup()`. Fails on duplicate IDs. `ExerciseId = Literal[IDS]` fixes the allowed exercise IDs at this point, for plans and for the LLM's output.
- `assistant.py`: reads `prompts.yaml` into `PROMPTS`.
- `main.py`: sets up logging and creates the FastAPI `app`.

So a change to `.env`, `prompts.yaml` or `exercises.yaml` needs a restart. `run.sh` starts uvicorn with `--reload-include "*.yaml"`, so YAML changes restart it by themselves. A trainee's plan is read from the db on every reply, so changing it with `scripts/add_trainee.py` needs no restart.

## Flow 1: a trainee sends a WhatsApp message

This is the main flow. Example: the trainee writes *"yanek dzisiaj waga 80,1 kg"*.

```
Meta ──POST /webhook──► main.receive_webhook()
  ├─ whatsapp.verify_signature(app_secret, body, X-Hub-Signature-256)   wrong → 401
  ├─ whatsapp.failed_statuses(payload)        → log a warning for each failed delivery
  ├─ whatsapp.parse_messages(payload)         → list[InboundMessage(wamid, phone, type, text)]
  └─ for each message:
       ├─ trainees.find_by_phone(session, phone)   → Trainee or None
       ├─ INSERT Message(direction="in", ...)   into the messages table
       │     registered:     with body, trainee_id, trainer_id
       │     not registered: without the text (only what dedup needs)
       │     wamid already there (Meta redelivered) → rollback, skip it
       ├─ not registered        → background.add_task(reply_unregistered, msg)
       ├─ mentions_bot(text)    → background.add_task(reply, message.id)
       └─ otherwise             → nothing; the message waits as history
  ◄── 200 {"status": "ok"}      (Meta gets the answer right away; the reply happens after)

main.reply(message_id)                          runs after the 200, in the background
  ├─ load the Message and its Trainee from the db
  ├─ conversation.recent_history(session, trainee.id, now)
  │     → the trainee's last 20 messages from the last 24 h, oldest first (incl. this one)
  ├─ assistant.generate_reply(settings, trainee, history, now)   → LlmOutput   (see Flow 2)
  │     uses the trainee's plan (plans.of(trainee)) and timezone
  │     any exception → result = None
  ├─ conversation.asked_again(session, message)
  │     the trainee called the bot again after this message, with no bot message in between
  │     → stop: the reply to that later message covers this one (its history has both)
  ├─ save_report(session, trainee, message, result)   → the text to send
  │     result None → APOLOGY ("Przepraszam, mam chwilowy problem...")
  │     entries.save(session, trainee, message, result)   (see Flow 2b)
  │         fails → APOLOGY (the reply would confirm something that isn't saved)
  │     otherwise → result.reply, and log "Saved weight/training from ..."
  └─ conversation.send_and_store(settings, trainee, text)
        ├─ whatsapp.send_text()  → POST graph.facebook.com/.../messages → wamid
        └─ INSERT Message(direction="out", trainee_id, trainer_id, ...)

main.reply_unregistered(msg)
  └─ whatsapp.send_text(phone, UNREGISTERED)   "Ten numer nie jest zarejestrowany – ..."; not stored
```

Things to notice:

- **Every message from a trainee is stored**, with or without "yanek". Messages without the name become context the next time the bot is asked.
- **Unknown numbers** get the fixed reply to every message and never reach the LLM. Register yourself with `scripts/add_trainee.py` (Flow 5), or the bot answers you with that too.
- **Dedup is the unique `wamid` column.** Meta sometimes delivers the same webhook twice; the second insert fails and is skipped, so the bot doesn't answer twice.
- **The reply runs after the HTTP response.** Meta wants a fast 200; an OpenAI call can take seconds. In production this becomes a Cloud Task (that's why `reply()` takes a message ID: it's what a task would carry).
- **Saving comes before sending.** The trainee only gets a confirmation for what is actually in the db.
- **Errors never reach the trainee as a stack trace.** A failed LLM call or save sends `APOLOGY`; a failed send is only logged.

`GET /webhook` (`verify_webhook()`) is separate: Meta calls it once when the Callback URL is set, and the bot answers with `hub.challenge` if `hub.verify_token` matches `WHATSAPP_VERIFY_TOKEN`. `GET /` is a plain health check (open `<tunnel url>/` in a browser to see that the bot is reachable).

## Flow 2: the LLM call

What happens inside `assistant.generate_reply(settings, trainee, history, now)`:

```
generate_reply()
  ├─ plan = plans.of(trainee)              the JSON in trainees.training_plan → TrainingPlan
  ├─ build_instructions(plan, trainee.name)  → the system prompt (one string):
  │     PROMPTS["system"]                     who Yanek is, all extraction and reply rules
  │   + PROMPTS["trainee_name"]               "…ma na imię Kasia." (zrobiłaś / zrobiłeś)
  │   + PROMPTS["training_plan"]              with plan.prompt_text() filled in (below)
  │     (or PROMPTS["no_training_plan"] when the trainee has no plan)
  ├─ reply_input(settings, history)       → the chat turns, shaped by HISTORY_MODE:
  │     split_at_last_bot_message(history) → (context, new)
  │         new = everything after the bot's last message (the report being sent now)
  │     HISTORY_MODE=marked (default):  context + developer "--- NOWE WIADOMOŚCI ---" + new
  │     HISTORY_MODE=new_only:          new only (the code drops the context)
  │     no bot message in the history:  everything is new, no marker
  │   to_llm_messages() turns stored messages into [{"role": "user"|"assistant", "content"}]:
  │     "in" → user, "out" → assistant; non-text → "[wiadomość typu image]";
  │     leading assistant turns are dropped (the conversation must start with the trainee)
  ├─ + {"role": "developer", "content": _today(trainee, now)}
  │     "Dzisiaj jest wtorek, 2026-11-03, godz. 10:15." in the trainee's timezone
  └─ _complete(settings, instructions, messages, today, plan)
       ├─ no OPENAI_API_KEY → MissingApiKeyError
       └─ up to ATTEMPTS (2) times:
            ├─ client.responses.create(model=OPENAI_MODEL, instructions, input=messages,
            │       text.format = strict json_schema from llm_output.json_schema(),
            │       reasoning effort low, store=False)
            ├─ a "refusal" part in the output → return LlmOutput(reply=REFUSAL)
            ├─ empty output_text → RuntimeError
            ├─ llm_output.validate_output(raw, today, plan)   → LlmOutput   (see below)
            └─ InvalidOutputError:
                 first attempt → append the model's raw answer + a developer message
                                 "Twoja odpowiedź nie przeszła walidacji: - <errors>"
                                 and try again
                 last attempt  → raise (main.reply() then sends APOLOGY)
```

The order of the prompt matters for cost: the part that is the same for everyone (the rules) comes first and the per-trainee parts (plan, history, today's date) come later, so OpenAI can cache the shared prefix.

### What the model sees of the plan

The whole exercise catalog is **not** in the prompt, only the plan's exercises. `plan.prompt_text()` gives the trainer's notes, then each day with its exercises: ID, catalog name, aliases, dose, and on the next line the replacements the trainer allows:

```
Dzień A – poniedziałek (dół ciała)
- back_squat: Przysiad ze sztangą (też: przysiad, przysiady, siady, squat, back squat) – 4 x 6–8, ok. 60 kg
  zamienniki: leg_press: Suwnica na nogi (też: suwnica, ...); bulgarian_split_squat: Przysiad bułgarski (też: ...)
```

That's enough for "suwnica zamiast siadów" (`leg_press` replacing `back_squat`). For an exercise that isn't in the plan or its replacements, the model gives no ID, only the full Polish name. `validate_output()` then finds the ID in the catalog by name or alias (`exercises.lookup()`).

### What the model returns

The model must answer with JSON matching `LlmOutput` (strict mode, so the structure is guaranteed by OpenAI):

```json
{
  "reply": "Zapisałem 80,1 kg na dziś. 💪",
  "weight": {"kg": 80.1, "date": "2026-11-03"},
  "training": null
}
```

`training`, when a session is reported, has `date`, `plan_day` (copied exactly from the plan), `status` (`done` / `partial` / `skipped`), `exercises`, `missed_exercises` and `notes`. `plan_day` is the day's name. `missed_exercises` isn't in the schema the model gets: the code fills it in, along with `done`/`partial` (step 6 below). Each exercise has `exercise_id` (a catalog ID or null), `name`, `in_plan`, `sets`, `reps`, `weight_kg`, `replaces` (the ID of the plan exercise it was done instead of) and `comment`. What each field means is explained to the model in `prompts.yaml`, in Polish.

For a correction or addition to a training reported earlier ("sorki, przysiad był na 65 kg"), the prompt tells the model to return the whole session again, with the change, because the save replaces the day (Flow 2b). The model can only do that if the earlier report is in the history it gets.

### `validate_output(raw, today, plan)`

`plan` is the trainee's `TrainingPlan` or None.

OpenAI guarantees the structure; this function checks that the **values** make sense, and collects every problem before raising so the retry can fix them all at once:

1. Parse into `LlmOutput` with pydantic. Wrong types, missing fields, extra fields, an exercise ID not in the catalog → errors like `weight.kg: Input should be a valid number`.
2. `reply`: not empty, not JSON, at most 4096 characters (WhatsApp's limit), only Latin letters (Polish letters and emoji are fine; Cyrillic or Armenian slip-ins are not).
3. `weight`: 30–300 kg, date not in the future and not more than 31 days ago.
4. `training`: same date window; `skipped` has no exercises, `done`/`partial` have at least one; sets 1–20; load 0–500 kg; a `replaces` exercise has `in_plan: false`.
5. Against the plan (`_check_against_plan()`): `plan_day` must be one of the plan's days (case and spacing don't matter; it's set to the plan's exact name). An `in_plan` exercise needs the ID of an exercise planned for that day. A planned ID with `in_plan: false` is wrong unless it replaces something. `replaces` must be an ID planned for that day. Without `plan_day`, any day of the plan counts. Without a plan, `plan_day`, `in_plan` and `replaces` must all be empty.
6. Everything passed, so the code fills in and fixes what it can itself. `**bold**` (Markdown) becomes `*bold*` (WhatsApp) in the reply. `_fill_in_missed()` sets `missed_exercises` to the plan day's exercises that aren't in the report as done (`in_plan`) or replaced (`replaces`): what the trainee didn't report counts as not done. It then sets the status to `done` if nothing is missed, otherwise `partial`. `skipped` stays, with the whole day missed. Without a plan day, nothing is missed. `_match_to_catalog()` gives every exercise that isn't in the plan or its replacements the catalog ID matching its name ("Pompki" → `push_up`), keeps the model's own ID if the name matches nothing, and leaves it null if there's neither. Then the `LlmOutput` is returned.

## Flow 2b: saving the extracted data

`entries.save(session, trainee, message, output, daily_updates_only)`, called from `main.save_report()` with `DAILY_UPDATES_ONLY` from `.env`:

```
entries.save()
  ├─ output.weight   → _save_weight()
  │     _entry_to_fill() picks the row:
  │       DAILY_UPDATES_ONLY=False (default) → always a new WeightEntry (INSERT)
  │       DAILY_UPDATES_ONLY=True → the trainee's newest WeightEntry for weight.date
  │           none          → a new one (INSERT)
  │           saved from a newer message (entry.message_id > message.id) → leave it, stop
  │           otherwise     → that row (UPDATE)
  │     set kg, message_id = the message the bot is answering
  ├─ output.training → _save_training()
  │     a TrainingCheckin for training.date, the same way
  │     set plan_day, status, missed_exercises, notes, message_id
  │     checkin.exercises = [TrainingExercise(**ex) for each exercise]
  │         on an updated check-in, the old exercise rows are deleted (delete-orphan)
  └─ COMMIT   (SQLAlchemy sends the INSERT or UPDATE here)
```

- **By default nothing is overwritten.** "yanek 80" then "yanek sorki, 81" leaves two rows for the day, 80 and 81. The current value of a day is its newest row (highest `id`); that's what the dashboard should show, and the older rows are the history of corrections. A corrected training is likewise a whole new check-in with its own exercises.
- **`DAILY_UPDATES_ONLY=True`** keeps one row per trainee per day: the same two messages leave one row, 81. If a day already has several rows (saved while the setting was off), the newest one is updated.
- **The "newer message" check** (only with `DAILY_UPDATES_ONLY=True`) is for replies that finish out of order: an older report never overwrites a newer one. By default both are kept anyway, and the one from the newer message has the higher `message_id`.
- Messages sent close together ("yanek 80", "yanek sorki, 81" before the first reply is out) still give one row: `asked_again()` in Flow 1 skips the first, and the second's LLM call sees both and returns 81. The 80 stays in `messages`.
- `trainer_id` is copied from the trainee onto every entry, so the dashboard can filter every query by trainer.

## Flow 3: the bot starts a conversation itself

Only the script `scripts/send_test_custom_first_message.py` does this so far; later the scheduler will (reminders).

```
send_test_custom_first_message.py
  ├─ trainees.find_by_phone(to)              not registered → exit ("run scripts/add_trainee.py")
  ├─ conversation.recent_history(), conversation.in_service_window()
  ├─ conversation.compose_first_message(settings, trainee, history, now)
  │     HARDCODED_FIRST_MESSAGE=True → utils.hardcoded_first_message()
  │     otherwise → assistant.generate_first_message()
  │         same as Flow 2, but build_instructions() also appends PROMPTS["first_message_task"],
  │         and the last turn is a fake user turn "[automatyczny wyzwalacz] Dzisiaj jest ..."
  │         only .reply is used, nothing is saved
  ├─ --dry-run → print and stop
  ├─ no inbound message in 24 h → warning (WhatsApp will probably drop it)
  └─ conversation.send_and_store()
```

## Flow 4: `bash run.sh`

```
run.sh
  1. uv run alembic upgrade head            create/upgrade wabot.db
  2. uv run python scripts/send_test.py     template message to your phone = token works
  3. (unless TUNNEL=0) start cloudflared → .cloudflared.log, killed again on exit
  4. in the background:
       wait up to 30 s for https://xxx.trycloudflare.com in the log
       → scripts/set_webhook.py <url>
           app ID from WHATSAPP_APP_ID or GET /app with the token
           every 5 s, for up to 2 minutes (WAIT_FOR_TUNNEL, RETRY_EVERY):
             POST /{app_id}/subscriptions with callback_url=<url>/webhook,
                  the verify token and the app token (app ID | app secret)
               → Meta immediately calls GET <url>/webhook (verify_webhook) to check it
             Meta reached the bot → print "Webhook set: <url>/webhook", done
             Meta couldn't (tunnel not live yet, bot not up) → try again
           still failing after 2 minutes → exit with Meta's error;
             run.sh then prints "WARNING: set the Callback URL by hand: <url>/webhook"
  5. uv run uvicorn wabot.main:app --reload --reload-include "*.yaml"   (foreground)
```

Steps 4 and 5 run at the same time: `set_webhook.py` keeps retrying until the bot started in step 5 answers Meta through the tunnel.

- **Wait for `Webhook set: …`** before sending a message. Until then Meta still sends messages to the previous tunnel, which is gone, and they're lost (Meta doesn't redeliver them to the new URL).
- **Every run gets a new tunnel address**, so the webhook is set again on every start. Ctrl+C stops uvicorn and cloudflared together. If port 8000 is still taken afterwards, an old uvicorn is still running. Stop it before starting again.
- **Meta's check is the test, not one from your laptop.** A new trycloudflare.com address can take a while to resolve on your machine (a VPN's DNS, for example) even though Meta already reaches it, so the script doesn't try the URL itself.

## Flow 5: registering a trainee (`scripts/add_trainee.py`)

Until the dashboard exists, this is the only way to add a trainee or change their plan.

```
add_trainee.py [--phone] [--name] [--trainer] [--plan FILE | --no-plan] [--consent-date] [--timezone]
  ├─ phone = --phone or WHATSAPP_TEST_RECIPIENT → trainees.normalize_phone()  ("+48 111..." → "48111...")
  ├─ plans.load(--plan or plans/example.yaml)   YAML → TrainingPlan
  │     an ID not in exercises.yaml, a typo in a key, an empty day → exit with the error
  ├─ trainees.find_by_phone()
  │     none  → new Trainee with defaults: "Podopieczny testowy", plans/example.yaml,
  │             consent date today, Europe/Warsaw, trainer --trainer or "Trener testowy"
  │             (trainees.trainer_named() finds the trainer or creates it)
  │     found → only the options you pass change
  └─ COMMIT, print what is saved
```

With no options it registers your own number with the example plan, which is all you need for local testing. The plan is saved as JSON (`plans.to_db()`).

A plan file looks like this (see `plans/example.yaml`). Every `id` and every replacement is an ID from `src/wabot/exercises.yaml`, and everything apart from `id` is optional:

```yaml
notes: |
  Cel: redukcja. We wtorek i sobotę 8–10 tys. kroków.
days:
  - name: Dzień A – poniedziałek (dół ciała)
    exercises:
      - id: back_squat
        sets: 4
        reps: 6–8
        weight: ok. 60 kg
        replacements: [leg_press, bulgarian_split_squat]
```

## The database

`models.py`; changing a model means a new Alembic migration (see CLAUDE.md for the command).

```
trainers ──< trainees ──< messages
                     ──< weight_entries        (per report; the newest per date is current)
                     ──< training_checkins     (per report; the newest per date is current) ──< training_exercises
```

| Table | Columns | |
|---|---|---|
| `trainers` | `id`, `name`, `created_at` | created by `scripts/add_trainee.py` for now |
| `trainees` | `id`, `trainer_id`, `phone` (unique), `name`, `timezone`, `consent_date`, `training_plan` (JSON), `created_at` | `phone` is how a message finds its trainee; `training_plan` is a `plans.TrainingPlan`, read with `plans.of(trainee)` |
| `messages` | `id`, `wamid` (unique), `direction` (`in`/`out`), `phone`, `type`, `body`, `trainee_id`, `trainer_id`, `created_at` | chat history and webhook dedup; `body` and `trainee_id` are null for unregistered numbers (and for messages from before migration 0002) |
| `weight_entries` | `id`, `trainer_id`, `trainee_id`, `date`, `kg`, `message_id`, `created_at`, `updated_at` | |
| `training_checkins` | `id`, `trainer_id`, `trainee_id`, `date`, `plan_day`, `status`, `missed_exercises` (JSON list of exercise IDs), `notes`, `message_id`, `created_at`, `updated_at` | |
| `training_exercises` | `id`, `checkin_id`, `exercise_id`, `name`, `in_plan`, `sets`, `reps`, `weight_kg`, `replaces`, `comment` | same fields as `llm_output.Exercise`; `exercise_id` is the catalog ID progress is charted by, `replaces` the ID of the replaced plan exercise |

- `message_id` on an entry is the trainee's message the bot answered when it saved the entry (the "yanek ..." one), so the dashboard can show the original message next to it.
- All timestamps are UTC (`UTCDateTime`, so SQLite and Postgres behave the same). `date` columns are the trainee's local day.
- **Reading the current value**: the newest row per trainee and date, e.g. `SELECT * FROM weight_entries w WHERE id = (SELECT MAX(id) FROM weight_entries WHERE trainee_id = w.trainee_id AND date = w.date)`. An index on `(trainee_id, date)` keeps it fast. Migration 0004 replaced the old one-per-day unique constraint with it.
- Migration 0003 turned `training_plan` from text into JSON and cleared the old free-text plans. Register a plan again with `scripts/add_trainee.py --plan plans/example.yaml`.
- On SQLite, `alembic/env.py` turns foreign keys off while migrating. Batch mode changes a table by copying it and dropping the old one, which foreign keys would block.

To look at the data, open `wabot.db` in any SQLite browser (DB Browser for SQLite, or a SQLite extension for VS Code).

## Where to change what

| I want to... | Change |
|---|---|
| change what Yanek says or how it extracts | `src/wabot/prompts.yaml` (`system`) |
| add a field to the extracted JSON | the pydantic model in `llm_output.py`, the rules in `prompts.yaml`, a check in `validate_output()` if needed, a column in `models.py` + a migration, tests in `tests/test_llm_output.py` and `tests/test_entries.py` |
| add an exercise | a new entry in `exercises.yaml` (never rename an existing ID); plans can use it right away |
| teach the bot a nickname ("siady") | add it to the exercise's `aliases` in `exercises.yaml` |
| change a trainee's training plan | edit a copy of `plans/example.yaml`, then `uv run python scripts/add_trainee.py --plan path/to/plan.yaml` |
| change how the plan looks to the LLM | `TrainingPlan.prompt_text()` in `plans.py`, and the `training_plan` text in `prompts.yaml` that explains the format |
| register another trainee | `uv run python scripts/add_trainee.py --phone 48... --name ...` |
| change the reply for unknown numbers | `UNREGISTERED` in `main.py` |
| change the trigger word | `BOT_NAME` in `conversation.py` |
| change how much history the model sees | `HISTORY_WINDOW` / `HISTORY_LIMIT` in `conversation.py` |
| change the model | `OPENAI_MODEL` in `.env` (default in `config.py`) |
| change the retry count | `ATTEMPTS` in `assistant.py` |
| change plausibility limits | constants at the top of `llm_output.py` |
| see how the real model handles a month of reports | `uv run python scripts/simulate_month.py` (`--only tomek` for one trainee): the 45 messages of `scenarios/*.yaml` in time order through `main.reply()` at simulated times, into `simulated_month.db` (never `wabot.db`), nothing sent to WhatsApp; prints each reply and what was saved, then each trainee's current values per day |
| add a simulated trainee or message | a new `scenarios/<name>.yaml` (or a report in one): `text`, the answer a correct model gives, and `saved` where the code must change it; the month test checks it and the script sends it |

## Not built yet

- **Corrections of older reports.** The LLM doesn't see what's saved, only the chat history. A correction of a training whose report is no longer in the history (older than 24 h or 20 messages, or always with `HISTORY_MODE=new_only`) replaces that day with just the corrected part. Milestone 3: show the LLM the saved entries of the last days.
- **Diet reports** are only acknowledged, not extracted or saved.
- No dashboard, no reminders, no photos.

## Tests

`uv run pytest`. `tests/conftest.py` points the app at a temporary SQLite db (tables recreated for every test) and sets fake tokens and keys as environment variables, which win over `.env`. No test calls WhatsApp or OpenAI.

- `test_webhook.py`: Flow 1 through FastAPI's test client, with the LLM and `send_text` replaced by fakes: replies, dedup, unregistered numbers, saving, corrections, a failed save, a message followed up on. Its `add_trainee()` helper is used by the other test files.
- `test_entries.py`: Flow 2b: one entry per day, corrections, out-of-order saves, exercises replaced.
- `test_conversation.py`: the name trigger, history per trainee, `asked_again()`, plan injection and the plan text, bad plans, the example plan, `exercises.lookup()`, the trainee's local date, `scripts/add_trainee.py`.
- `test_month_scenario.py`: the three trainees of `scenarios/` (15 reports each in October 2026, three plans, two trainers), interleaved in time order through the whole reply flow (`simulation.Replay` → `main.reply()` → `_complete()` with a fake OpenAI client returning the scenario's answer → `validate_output()` → `entries.save()`). After every message it checks that the model got only that trainee's messages and the simulated date, and what was saved: weight, plan day, status, missed exercises, exercise IDs and replacements. Runs in both `DAILY_UPDATES_ONLY` modes. Some answers say `done` where the code must make it `partial`.
- `test_llm_output.py`: `validate_output()` accepting and rejecting outputs (incl. the checks against the plan's IDs and catalog matching by name), the retry in `_complete()` with a fake OpenAI client, the schema.
