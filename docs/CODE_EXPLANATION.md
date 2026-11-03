# How the code works

A walk through what happens at runtime: which function calls which, in what order, and where data ends up. It describes flows, not every function. For *why* things are built this way, see [PLAN.md](PLAN.md).

**Keep this file in sync with the code.** When a change adds, removes or reorders a step in one of the flows below, update the flow in the same change.

## The files in one line each

| File | Role |
|---|---|
| `src/wabot/main.py` | FastAPI app: the HTTP endpoints Meta calls, and `reply()`, the background job that answers a message |
| `src/wabot/whatsapp.py` | Talking to Meta: check the webhook signature, parse the payload, send a text |
| `src/wabot/conversation.py` | The glue: "is the bot mentioned?", chat history from the db, the trainee's plan, send + store |
| `src/wabot/assistant.py` | Talking to OpenAI: build the prompt, call the model, retry on invalid output |
| `src/wabot/llm_output.py` | The shape of the model's answer (pydantic models → JSON schema) and `validate_output()` |
| `src/wabot/prompts.yaml` | The Polish prompt texts |
| `src/wabot/exercises.yaml` / `exercises.py` | The exercise catalog: fixed IDs, names, aliases |
| `src/wabot/models.py` | Database tables (only `messages` so far) |
| `src/wabot/db.py` | Database engine and sessions |
| `src/wabot/config.py` | `Settings`: every value from `.env` |
| `src/wabot/utils.py` | `hardcoded_first_message()`, a fixed text used instead of the LLM |
| `plans/example.txt` | The training plan every trainee gets for now |
| `run.sh` | Local start: migrate, test message, tunnel, webhook, uvicorn |
| `scripts/` | One-off command-line tools (see the end of this file) |

## What happens at import / startup

Some files do work the moment they're imported, once per process:

- `config.py`: nothing yet; `get_settings()` reads `.env` when it's first called and caches the result.
- `db.py`: creates the SQLAlchemy `engine` from `DATABASE_URL` (default `sqlite:///wabot.db`) and the `SessionLocal` session factory. For SQLite it turns on WAL, a 5 s busy timeout and foreign keys.
- `exercises.py`: reads `exercises.yaml` into `CATALOG` (a list of `CatalogExercise`) and `IDS` (a tuple of the IDs). Fails on duplicate IDs.
- `llm_output.py`: builds the pydantic models. `ExerciseId = Literal[exercises.IDS]`, so the allowed exercise IDs are fixed at this point.
- `assistant.py`: reads `prompts.yaml` into `PROMPTS`.
- `main.py`: sets up logging and creates the FastAPI `app`.

So a change to `.env`, `prompts.yaml` or `exercises.yaml` needs a restart. `run.sh` starts uvicorn with `--reload-include "*.yaml"`, so YAML changes restart it by themselves.

## Flow 1: a trainee sends a WhatsApp message

This is the main flow. Example: the trainee writes *"yanek dzisiaj waga 80,1 kg"*.

```
Meta ──POST /webhook──► main.receive_webhook()
  ├─ whatsapp.verify_signature(app_secret, body, X-Hub-Signature-256)   wrong → 401
  ├─ whatsapp.failed_statuses(payload)        → log a warning for each failed delivery
  ├─ whatsapp.parse_messages(payload)         → list[InboundMessage(wamid, phone, type, text)]
  └─ for each message:
       ├─ INSERT Message(direction="in", ...)   into the messages table
       │     wamid already there (Meta redelivered) → rollback, skip it
       └─ background.add_task(reply, msg)
  ◄── 200 {"status": "ok"}      (Meta gets the answer right away; the reply happens after)

main.reply(msg)                                 runs after the 200, in the background
  ├─ conversation.mentions_bot(msg.text)        no "yanek"/"yanku"/... → stop, nothing else happens
  ├─ conversation.recent_history(session, phone, now)
  │     → up to 20 messages from the last 24 h, oldest first (this includes msg itself)
  ├─ conversation.training_plan_for(settings, phone)
  │     → text of TRAINING_PLAN_FILE (plans/example.txt), or None
  ├─ assistant.generate_reply(settings, history, now, plan)   → LlmOutput   (see Flow 2)
  │     any exception → text = APOLOGY ("Przepraszam, mam chwilowy problem...")
  ├─ log "Extracted weight/training ..."   the JSON is only logged; nothing saves it yet
  └─ conversation.send_and_store(settings, phone, result.reply)
        ├─ whatsapp.send_text()  → POST graph.facebook.com/.../messages → wamid
        └─ INSERT Message(direction="out", ...)
```

Things to notice:

- **Every message is stored**, with or without "yanek". Messages without the name become context the next time the bot is asked.
- **Dedup is the unique `wamid` column.** Meta sometimes delivers the same webhook twice; the second insert fails and is skipped, so the bot doesn't answer twice.
- **The reply runs after the HTTP response.** Meta wants a fast 200; an OpenAI call can take seconds. In production this becomes a Cloud Task.
- **Errors never reach the trainee as a stack trace.** A failed LLM call sends `APOLOGY`; a failed send is only logged.

`GET /webhook` (`verify_webhook()`) is separate: Meta calls it once when the Callback URL is set, and the bot answers with `hub.challenge` if `hub.verify_token` matches `WHATSAPP_VERIFY_TOKEN`. `GET /` is a health check that `set_webhook.py` uses to see that the tunnel is up.

## Flow 2: the LLM call

What happens inside `assistant.generate_reply(settings, history, now, plan)`:

```
generate_reply()
  ├─ build_instructions(plan)             → the system prompt (one string):
  │     PROMPTS["system"]                     who Yanek is, all extraction and reply rules
  │   + PROMPTS["exercise_catalog"]           with exercises.catalog_text() filled in
  │   + PROMPTS["training_plan"]              with the plan text filled in
  │     (or PROMPTS["no_training_plan"] when there is no plan)
  ├─ reply_input(settings, history)       → the chat turns, shaped by HISTORY_MODE:
  │     split_at_last_bot_message(history) → (context, new)
  │         new = everything after the bot's last message (the report being sent now)
  │     HISTORY_MODE=marked (default):  context + developer "--- NOWE WIADOMOŚCI ---" + new
  │     HISTORY_MODE=new_only:          new only (the code drops the context)
  │     no bot message in the history:  everything is new, no marker
  │   to_llm_messages() turns stored messages into [{"role": "user"|"assistant", "content"}]:
  │     "in" → user, "out" → assistant; non-text → "[wiadomość typu image]";
  │     leading assistant turns are dropped (the conversation must start with the trainee)
  ├─ + {"role": "developer", "content": _today()}   "Dzisiaj jest wtorek, 2026-11-03, godz. 10:15."
  └─ _complete(settings, instructions, messages, now, plan)
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
  "reply": "Zanotowałem 80,1 kg na dziś. 💪",
  "weight": {"kg": 80.1, "date": "2026-11-03"},
  "training": null
}
```

`training`, when a session is reported, has `date`, `plan_day` (copied exactly from the plan), `status` (`done` / `partial` / `skipped`), `exercises`, `missed_exercises` and `notes`. Each exercise has `exercise_id` (a catalog ID or null), `name`, `in_plan`, `sets`, `reps`, `weight_kg`, `replaces` (the plan exercise it was done instead of) and `comment`. What each field means is explained to the model in `prompts.yaml`, in Polish.

### `validate_output(raw, today, plan)`

OpenAI guarantees the structure; this function checks that the **values** make sense, and collects every problem before raising so the retry can fix them all at once:

1. Parse into `LlmOutput` with pydantic. Wrong types, missing fields, extra fields, an exercise ID not in the catalog → errors like `weight.kg: Input should be a valid number`.
2. `reply`: not empty, not JSON, at most 4096 characters (WhatsApp's limit), only Latin letters (Polish letters and emoji are fine; Cyrillic or Armenian slip-ins are not).
3. `weight`: 30–300 kg, date not in the future and not more than 31 days ago.
4. `training`: same date window; `skipped` has no exercises, `done`/`partial` have at least one; sets 1–20; load 0–500 kg; a `replaces` exercise has `in_plan: false` and isn't also listed as missed.
5. Against the plan: `plan_day`, every `in_plan` exercise name, every missed exercise and every `replaces` must appear in the plan text (case-insensitive substring). Without a plan, none of them may be set.
6. Everything passed: `**bold**` (Markdown) becomes `*bold*` (WhatsApp) in the reply, then the `LlmOutput` is returned.

## Flow 3: the bot starts a conversation itself

Only the script `scripts/send_test_custom_first_message.py` does this so far; later the scheduler will (reminders).

```
send_test_custom_first_message.py
  ├─ conversation.recent_history(), conversation.in_service_window()
  ├─ conversation.compose_first_message(settings, phone, history, now)
  │     HARDCODED_FIRST_MESSAGE=True → utils.hardcoded_first_message()
  │     otherwise → assistant.generate_first_message()
  │         same as Flow 2, but build_instructions() also appends PROMPTS["first_message_task"],
  │         and the last turn is a fake user turn "[automatyczny wyzwalacz] Dzisiaj jest ..."
  │         only .reply is used
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
           wait until GET <url>/ returns 200 (the bot is up behind the tunnel)
           POST /{app_id}/subscriptions with callback_url=<url>/webhook
             → Meta immediately calls GET /webhook (verify_webhook) to check it
  5. uv run uvicorn wabot.main:app --reload --reload-include "*.yaml"   (foreground)
```

Steps 4 and 5 run at the same time: `set_webhook.py` waits for the bot started in step 5.

## The database

One table so far, `messages` (`models.Message`):

| Column | |
|---|---|
| `id` | primary key |
| `wamid` | WhatsApp message ID, unique; the webhook dedup |
| `direction` | `in` (from the trainee) or `out` (from the bot) |
| `phone` | the trainee's number |
| `type` | `text`, `image`, `template`, ... |
| `body` | the text; null for non-text messages |
| `created_at` | UTC (`UTCDateTime`, so SQLite and Postgres behave the same) |

It's both the chat history the LLM sees and the dedup. Changing a model means a new Alembic migration (see CLAUDE.md for the command).

## Where to change what

| I want to... | Change |
|---|---|
| change what Yanek says or how it extracts | `src/wabot/prompts.yaml` (`system`) |
| add a field to the extracted JSON | the pydantic model in `llm_output.py`, the rules in `prompts.yaml`, a check in `validate_output()` if needed, tests in `tests/test_llm_output.py` |
| add an exercise | a new entry in `exercises.yaml` (never rename an existing ID) |
| change the training plan | `plans/example.txt`, or `TRAINING_PLAN_FILE` in `.env` |
| change the trigger word | `BOT_NAME` in `conversation.py` |
| change how much history the model sees | `HISTORY_WINDOW` / `HISTORY_LIMIT` in `conversation.py` |
| change the model | `OPENAI_MODEL` in `.env` (default in `config.py`) |
| change the retry count | `ATTEMPTS` in `assistant.py` |
| change plausibility limits | constants at the top of `llm_output.py` |

## Not built yet

- **The extracted weight/training is only logged** (`main.reply()`); no table stores it. That's the rest of milestone 2.
- **Every trainee gets the same plan** from a file; there are no trainer or trainee tables, and unknown numbers are answered like anyone else.
- No dashboard, no reminders, no photos.

## Tests

`uv run pytest`. `tests/conftest.py` points the app at a temporary SQLite db (tables recreated for every test) and sets fake tokens and keys as environment variables, which win over `.env`. No test calls WhatsApp or OpenAI.

- `test_webhook.py`: Flow 1 through FastAPI's test client, with the LLM and `send_text` replaced by fakes.
- `test_conversation.py`: the name trigger, history, plan injection, the catalog in the prompt.
- `test_llm_output.py`: `validate_output()` accepting and rejecting outputs, the retry in `_complete()` with a fake OpenAI client, the schema.
