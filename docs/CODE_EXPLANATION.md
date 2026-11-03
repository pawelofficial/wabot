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
| `src/wabot/entries.py` | Saving the extracted weight and training into the db (one per day, newer replaces older) |
| `src/wabot/prompts.yaml` | The Polish prompt texts |
| `src/wabot/exercises.yaml` / `exercises.py` | The exercise catalog: fixed IDs, names, aliases |
| `src/wabot/models.py` | Database tables (see the end of this file) |
| `src/wabot/db.py` | Database engine and sessions |
| `src/wabot/config.py` | `Settings`: every value from `.env` |
| `src/wabot/utils.py` | `hardcoded_first_message()`, a fixed text used instead of the LLM |
| `plans/example.txt` | Example training plan; `scripts/add_trainee.py` gives it to new trainees |
| `run.sh` | Local start: migrate, test message, tunnel, webhook, uvicorn |
| `scripts/` | One-off command-line tools (see Flows 3 and 5) |

## What happens at import / startup

Some files do work the moment they're imported, once per process:

- `config.py`: nothing yet; `get_settings()` reads `.env` when it's first called and caches the result.
- `db.py`: creates the SQLAlchemy `engine` from `DATABASE_URL` (default `sqlite:///wabot.db`) and the `SessionLocal` session factory. For SQLite it turns on WAL, a 5 s busy timeout and foreign keys.
- `exercises.py`: reads `exercises.yaml` into `CATALOG` (a list of `CatalogExercise`) and `IDS` (a tuple of the IDs). Fails on duplicate IDs.
- `llm_output.py`: builds the pydantic models. `ExerciseId = Literal[exercises.IDS]`, so the allowed exercise IDs are fixed at this point.
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
  │     uses trainee.training_plan and trainee.timezone
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
  ├─ build_instructions(trainee.training_plan)   → the system prompt (one string):
  │     PROMPTS["system"]                     who Yanek is, all extraction and reply rules
  │   + PROMPTS["exercise_catalog"]           with exercises.catalog_text() filled in
  │   + PROMPTS["training_plan"]              with the plan text filled in
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

The order of the prompt matters for cost: the parts that are the same for everyone (rules, catalog) come first and the per-trainee parts (plan, history, today's date) come later, so OpenAI can cache the shared prefix.

### What the model returns

The model must answer with JSON matching `LlmOutput` (strict mode, so the structure is guaranteed by OpenAI):

```json
{
  "reply": "Zapisałem 80,1 kg na dziś. 💪",
  "weight": {"kg": 80.1, "date": "2026-11-03"},
  "training": null
}
```

`training`, when a session is reported, has `date`, `plan_day` (copied exactly from the plan), `status` (`done` / `partial` / `skipped`), `exercises`, `missed_exercises` and `notes`. Each exercise has `exercise_id` (a catalog ID or null), `name`, `in_plan`, `sets`, `reps`, `weight_kg`, `replaces` (the plan exercise it was done instead of) and `comment`. What each field means is explained to the model in `prompts.yaml`, in Polish.

For a correction or addition to a training reported earlier ("sorki, przysiad był na 65 kg"), the prompt tells the model to return the whole session again, with the change, because the save replaces the day (Flow 2b). The model can only do that if the earlier report is in the history it gets.

### `validate_output(raw, today, plan)`

OpenAI guarantees the structure; this function checks that the **values** make sense, and collects every problem before raising so the retry can fix them all at once:

1. Parse into `LlmOutput` with pydantic. Wrong types, missing fields, extra fields, an exercise ID not in the catalog → errors like `weight.kg: Input should be a valid number`.
2. `reply`: not empty, not JSON, at most 4096 characters (WhatsApp's limit), only Latin letters (Polish letters and emoji are fine; Cyrillic or Armenian slip-ins are not).
3. `weight`: 30–300 kg, date not in the future and not more than 31 days ago.
4. `training`: same date window; `skipped` has no exercises, `done`/`partial` have at least one; sets 1–20; load 0–500 kg; a `replaces` exercise has `in_plan: false` and isn't also listed as missed.
5. Against the plan: `plan_day`, every `in_plan` exercise name, every missed exercise and every `replaces` must appear in the plan text (case-insensitive substring). Without a plan, none of them may be set.
6. Everything passed: `**bold**` (Markdown) becomes `*bold*` (WhatsApp) in the reply, then the `LlmOutput` is returned.

## Flow 2b: saving the extracted data

`entries.save(session, trainee, message, output)`, called from `main.save_report()`:

```
entries.save()
  ├─ output.weight   → _save_weight()
  │     the trainee's WeightEntry for weight.date
  │       none          → INSERT a new one
  │       saved from a newer message (entry.message_id > message.id) → leave it, stop
  │       otherwise     → replace it
  │     set kg, message_id = the message the bot is answering
  ├─ output.training → _save_training()
  │     the trainee's TrainingCheckin for training.date, same rules
  │     set plan_day, status, missed_exercises, notes, message_id
  │     checkin.exercises = [TrainingExercise(**ex) for each exercise]
  │         the old exercise rows are deleted (delete-orphan)
  └─ COMMIT
```

- **One weight and one training per trainee per day** (a unique constraint on `trainee_id, date`). That's how corrections work: "yanek 80" then "yanek sorki, 81" leaves one entry, 81.
- **The "newer message" check** is for replies that finish out of order. Together with `asked_again()` in Flow 1 it means an older report never overwrites a newer one.
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
  ├─ trainees.find_by_phone()
  │     none  → new Trainee with defaults: "Podopieczny testowy", plans/example.txt,
  │             consent date today, Europe/Warsaw, trainer --trainer or "Trener testowy"
  │             (trainees.trainer_named() finds the trainer or creates it)
  │     found → only the options you pass change
  └─ COMMIT, print what is saved
```

With no options it registers your own number with the example plan, which is all you need for local testing.

## The database

`models.py`; changing a model means a new Alembic migration (see CLAUDE.md for the command).

```
trainers ──< trainees ──< messages
                     ──< weight_entries        (one per trainee per date)
                     ──< training_checkins     (one per trainee per date) ──< training_exercises
```

| Table | Columns | |
|---|---|---|
| `trainers` | `id`, `name`, `created_at` | created by `scripts/add_trainee.py` for now |
| `trainees` | `id`, `trainer_id`, `phone` (unique), `name`, `timezone`, `consent_date`, `training_plan`, `created_at` | `phone` is how a message finds its trainee; `training_plan` goes into the prompt |
| `messages` | `id`, `wamid` (unique), `direction` (`in`/`out`), `phone`, `type`, `body`, `trainee_id`, `trainer_id`, `created_at` | chat history and webhook dedup; `body` and `trainee_id` are null for unregistered numbers (and for messages from before migration 0002) |
| `weight_entries` | `id`, `trainer_id`, `trainee_id`, `date`, `kg`, `message_id`, `created_at`, `updated_at` | |
| `training_checkins` | `id`, `trainer_id`, `trainee_id`, `date`, `plan_day`, `status`, `missed_exercises` (JSON list), `notes`, `message_id`, `created_at`, `updated_at` | |
| `training_exercises` | `id`, `checkin_id`, `exercise_id`, `name`, `in_plan`, `sets`, `reps`, `weight_kg`, `replaces`, `comment` | same fields as `llm_output.Exercise`; `exercise_id` is the catalog ID progress is charted by |

- `message_id` on an entry is the trainee's message the bot answered when it saved the entry (the "yanek ..." one), so the dashboard can show the original message next to it.
- All timestamps are UTC (`UTCDateTime`, so SQLite and Postgres behave the same). `date` columns are the trainee's local day.

To look at the data, open `wabot.db` in any SQLite browser (DB Browser for SQLite, or a SQLite extension for VS Code).

## Where to change what

| I want to... | Change |
|---|---|
| change what Yanek says or how it extracts | `src/wabot/prompts.yaml` (`system`) |
| add a field to the extracted JSON | the pydantic model in `llm_output.py`, the rules in `prompts.yaml`, a check in `validate_output()` if needed, a column in `models.py` + a migration, tests in `tests/test_llm_output.py` and `tests/test_entries.py` |
| add an exercise | a new entry in `exercises.yaml` (never rename an existing ID) |
| change a trainee's training plan | `uv run python scripts/add_trainee.py --plan path/to/plan.txt` |
| register another trainee | `uv run python scripts/add_trainee.py --phone 48... --name ...` |
| change the reply for unknown numbers | `UNREGISTERED` in `main.py` |
| change the trigger word | `BOT_NAME` in `conversation.py` |
| change how much history the model sees | `HISTORY_WINDOW` / `HISTORY_LIMIT` in `conversation.py` |
| change the model | `OPENAI_MODEL` in `.env` (default in `config.py`) |
| change the retry count | `ATTEMPTS` in `assistant.py` |
| change plausibility limits | constants at the top of `llm_output.py` |

## Not built yet

- **Corrections of older reports.** The LLM doesn't see what's saved, only the chat history. A correction of a training whose report is no longer in the history (older than 24 h or 20 messages, or always with `HISTORY_MODE=new_only`) replaces that day with just the corrected part. Milestone 3: show the LLM the saved entries of the last days.
- **Diet reports** are only acknowledged, not extracted or saved.
- No dashboard, no reminders, no photos.

## Tests

`uv run pytest`. `tests/conftest.py` points the app at a temporary SQLite db (tables recreated for every test) and sets fake tokens and keys as environment variables, which win over `.env`. No test calls WhatsApp or OpenAI.

- `test_webhook.py`: Flow 1 through FastAPI's test client, with the LLM and `send_text` replaced by fakes: replies, dedup, unregistered numbers, saving, corrections, a failed save, a message followed up on. Its `add_trainee()` helper is used by the other test files.
- `test_entries.py`: Flow 2b: one entry per day, corrections, out-of-order saves, exercises replaced.
- `test_conversation.py`: the name trigger, history per trainee, `asked_again()`, plan injection, the trainee's local date, the catalog in the prompt, `scripts/add_trainee.py`.
- `test_llm_output.py`: `validate_output()` accepting and rejecting outputs, the retry in `_complete()` with a fake OpenAI client, the schema.
