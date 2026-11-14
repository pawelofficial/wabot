# How the code works

A walk through what happens at runtime: which function calls which, in what order, and where data ends up. It describes flows, not every function. For *why* things are built this way, see [PLAN.md](PLAN.md).

**Keep this file in sync with the code.** When a change adds, removes or reorders a step in one of the flows below, update the flow in the same change.

## The files in one line each

| File | Role |
|---|---|
| `src/wabot/main.py` | FastAPI app: the HTTP endpoints Telegram and Meta call, `reply()` (the background job that answers a trainee), `reply_unregistered()`, `send_telegram_notice()` |
| `src/wabot/telegram.py` | Talking to Telegram: check the webhook's secret (derived from the bot token), parse an update, send a text (with the share-number button), set the webhook; errors without the token |
| `src/wabot/whatsapp.py` | Talking to Meta: check the webhook signature, parse the payload, send a text |
| `src/wabot/trainees.py` | Who is behind a phone number (`find_by_phone()`) or a Telegram chat (`find_by_telegram()`, `link_telegram()`), normalizing numbers, finding or creating a trainer |
| `src/wabot/conversation.py` | The glue: "is the bot mentioned?", chat history from the db (both channels), the `Turn` a reply works on (`load_turn()`: the messages still waiting are new, the handled ones before them context; `claim()` marks them handled), "did the trainee call the bot again?", send + store on a channel |
| `src/wabot/assistant.py` | Talking to OpenAI: build the prompt, call the model, retry on invalid output |
| `src/wabot/llm_output.py` | The shape of the model's answer (pydantic models → JSON schema) and `validate_output()` |
| `src/wabot/entries.py` | Saving the extracted weight and training into the db (a new row per report; `DAILY_UPDATES_ONLY` updates the day's row instead) |
| `src/wabot/prompts.yaml` | The Polish prompt texts |
| `src/wabot/exercises.yaml` / `exercises.py` | The exercise catalog: fixed IDs, names, aliases; `lookup()` finds an ID by name |
| `src/wabot/plans.py` | Training plans: days of catalog exercises with the trainer's replacements; loading, checking, the text the LLM sees |
| `src/wabot/models.py` | Database tables (see the end of this file) |
| `src/wabot/db.py` | Database engine and sessions |
| `src/wabot/config.py` | `Settings`: every value from `.env` |
| `src/wabot/logs.py` | Logging: the level (`LOG_LEVEL`), the console and a rotating file (`LOG_FILE`, 5 MB × 4) that also gets uvicorn's request lines; libraries that would print the Telegram token stay at WARNING |
| `src/wabot/dashboard/routes.py` | The trainers' dashboard pages under `/dashboard` (Flow 6), `render()` and the Polish formats (`pl_date`, `pl_month`, `pl_dose`, ...); `install(app)` adds them and the routers of `plan_pages.py` and `training_pages.py` to the app |
| `src/wabot/dashboard/auth.py` | Who is signed in (signed session cookie), `current_trainer` and `trainee_of_trainer`: the only place that decides which trainees a trainer may see |
| `src/wabot/dashboard/queries.py` | What the pages read: `current_per_day()` (the newest row of each day), `weigh_ins()` (each day's weight with the message it came from), `workouts()` (each day's current trainings, one of the plan and one outside it, with their letters), `conversations()` (the messages a report was saved from and the bot's answer, "Pokaż wiadomości") |
| `src/wabot/dashboard/training_pages.py` | Treningi: the month calendar and the month's trainings (Flow 6) |
| `src/wabot/plan_history.py` | A trainee's plan versions and the trainer's plan library: `assign()` (every change of a trainee's plan goes through it), `restore()`, `history()`, `library()` |
| `src/wabot/dashboard/plan_pages.py` | The plan pages: a trainee's plan (history, assigning from the library, the editor) and the plan library (`/dashboard/plans`) |
| `src/wabot/dashboard/plan_editor.py` | The plan editor's server side: the form's starting data, `parse()` (the submitted plan JSON checked by `plans.TrainingPlan`, errors in Polish) |
| `src/wabot/dashboard/templates/`, `static/` | `base.html` (the sidebar with the trainee dropdown) and one template per page; `app.css`, `app.js` (folding the sidebar, the phone drawer, the dropdown, the "Wybierz podopiecznego" hint), `weight_chart.js` (the weight chart, an SVG drawn in the browser), `plan_editor.js` (adding, removing and reordering days and exercises in the plan editor) |
| `src/wabot/utils.py` | `hardcoded_first_message()`, a fixed text used instead of the LLM |
| `plans/example.yaml` | Example training plan; `scripts/add_trainee.py` gives it to new trainees |
| `plans/upper_lower.yaml`, `plans/beginner_fbw.yaml` | Two more real plans: an upper/lower split on fixed weekdays, and two alternating full-body workouts for a beginner |
| `scenarios/*.yaml` | A month of reports per simulated trainee (Kasia, Tomek, Ola: one plan each, two trainers): the messages, the answer a correct model gives, and what must be saved. The format is explained at the top of `kasia.yaml` |
| `src/wabot/simulation.py` | Replays the scenarios through `main.reply()` at simulated times (used by the month test and `scripts/simulate_month.py`, not by the app) |
| `run.sh` | Local start: migrate, messenger check, tunnel, webhook (of `CHANNEL`), uvicorn |
| `run_dashboard.sh` | Local dashboard: uvicorn on port 8001 on `DATABASE_URL`'s database (migrated first) with the dev login |
| `scripts/setup_db.py` | Creates or migrates `DATABASE_URL`'s database (or `--new FILE`); `--simulated` backs it up to `db_backups/` and adds the simulated month (`simulation.load_canned()`: `register()` each scenario, then its reports through `main.reply()` with the correct answers) |
| `scripts/` | One-off command-line tools (see Flows 3 and 5) |

## What happens at import / startup

Some files do work the moment they're imported, once per process:

- `config.py`: nothing yet; `get_settings()` reads `.env` when it's first called and caches the result.
- `db.py`: creates the SQLAlchemy `engine` from `DATABASE_URL` (default `sqlite:///wabot.db`) and the `SessionLocal` session factory. For SQLite it turns on WAL, a 5 s busy timeout and foreign keys.
- `exercises.py`: reads `exercises.yaml` into `CATALOG` (a list of `CatalogExercise`), `IDS`, `BY_ID` and a name/alias → ID table for `lookup()`. Fails on duplicate IDs. `ExerciseId = Literal[IDS]` fixes the allowed exercise IDs at this point, for plans and for the LLM's output.
- `assistant.py`: reads `prompts.yaml` into `PROMPTS`.
- `telegram.py`: turns httpx's INFO logging off (each line has the request URL, which for Telegram has the bot token).
- `main.py`: sets up logging (`logs.setup()`: `LOG_LEVEL`, console + `LOG_FILE`) and creates the FastAPI `app`. When uvicorn starts it, it logs a warning if the active channel's settings are missing (`Settings.missing_for_channel()`).

So a change to `.env`, `prompts.yaml` or `exercises.yaml` needs a restart. `run.sh` starts uvicorn with `--reload-include "*.yaml"`, so YAML changes restart it by themselves. A trainee's plan is read from the db on every reply, so changing it with `scripts/add_trainee.py` needs no restart.

## Flow 1: a trainee sends a message (WhatsApp)

This is the main flow. Example: the trainee writes *"yanek dzisiaj waga 80,1 kg"*. The bot runs on Telegram now (`CHANNEL=telegram`); Telegram's webhook (Flow 1b) differs only in how it finds the trainee, and from `main.reply()` on everything is the same for both.

```
Meta ──POST /webhook──► main.receive_webhook()
  ├─ whatsapp.verify_signature(app_secret, body, X-Hub-Signature-256)   wrong → 401
  ├─ whatsapp.failed_statuses(payload)        → log a warning for each failed delivery
  ├─ whatsapp.parse_messages(payload)         → list[InboundMessage(wamid, phone, type, text)]
  │                                               text: a text's body, or an image's caption
  └─ for each message:
       ├─ trainees.find_by_phone(session, phone)   → Trainee or None
       ├─ INSERT Message(channel="whatsapp", external_id=wamid, direction="in", ...)
       │     registered:     with body, trainee_id, trainer_id
       │     not registered: without the text (only what dedup needs)
       │     external_id already there (Meta redelivered) → rollback, skip it
       ├─ not registered        → after_storing(reply_unregistered, msg)   (Flow 1c)
       ├─ mentions_bot(text)    → after_storing(reply, message.id)          (Flow 1c)
       └─ otherwise             → nothing; the message waits (handled_at null) for the next
                                  "yanek", whose reply reads it too
  ◄── 200 {"status": "ok"}      (Meta gets the answer right away; the reply happens after)

main.reply(message_id)                          runs after the 200, in the background
  ├─ conversation.not_answered_because(session, message)   → stop, without calling the LLM, when
  │     the message is handled already (another reply read it), or
  │     the trainee called the bot again later (called_again()): that reply reads this one too
  ├─ conversation.load_turn(session, message, now)   → Turn(trigger, trainee, context, new)
  │     history = recent_history(): the trainee's last 20 messages from the last 24 h, oldest first
  │     new     = from the first message still waiting (handled_at null) to the end, with the
  │               bot's messages in between (an apology, the help after /start)
  │     context = everything before it: handled, for what the new messages refer to
  ├─ ask_llm() → assistant.generate_reply(settings, trainee, context, new, now)   → LlmOutput
  │     (see Flow 2); uses the trainee's plan (plans.of(trainee)) and timezone
  │     any exception → result = None (a stored plan that no longer passes the checks too:
  │     the "Answering …" log line only notes it, plan_summary() → "not valid")
  ├─ save_report(session, turn, result)   one transaction → the text to send
  │     conversation.claim(): UPDATE the turn's waiting messages SET handled_at = now
  │     conversation.called_again() → rollback, send nothing (the later reply reads them)
  │     some were taken by another reply meanwhile (claim() False) → rollback, HandledMeanwhile:
  │         reply() starts again from not_answered_because() (TURN_ATTEMPTS = 2)
  │     result None → rollback → APOLOGY ("… Twoje wiadomości mam – napisz „yanek” za kilka
  │         minut …"): the messages keep waiting, so the next "yanek" reads them again
  │     entries.save(session, trainee, message, result)   (see Flow 2b) commits the marks too
  │         fails → rollback → APOLOGY (the reply would confirm something that isn't saved)
  │     otherwise → result.reply, and log "Saved weight/training from ..."
  └─ send_reply() → conversation.send_and_store(settings, trainee, text, message.channel)
        │     back where it came from; a failed send is only logged: the report is saved and
        │     its messages handled, so it isn't saved again
        ├─ conversation.send_text()
        │     "whatsapp" → whatsapp.send_text(phone)  → POST graph.facebook.com/.../messages → wamid
        │     "telegram" → telegram.send_text(chat_id, markdown=True) → "tg:<chat>:<id>"
        │                  (no chat yet → NotOnTelegramError, logged)
        └─ conversation.store_sent() → INSERT Message(channel, direction="out", ...)

main.reply_unregistered(msg)
  └─ whatsapp.send_text(phone, UNREGISTERED)   "Ten numer nie jest zarejestrowany – ..."; not stored
```

Things to notice:

- **Every message from a trainee is stored**, with or without "yanek". Messages without the name wait and are read with the next "yanek".
- **"Handled" is stored, not guessed** (`messages.handled_at`, decided 2026-11-13). A message is handled when a reply has saved what it reported, in the same transaction as the entries. Before, everything before the bot's last message counted as saved, so a reply that failed to send made the next "yanek" save the report again, and an apology or the help after `/start` made a report count as saved when it wasn't. Now a failed LLM call or save leaves the messages waiting (the apology says so), a failed send leaves them handled, and the bot's other messages don't move the line.
- **Unknown numbers** get the fixed reply to every message and never reach the LLM. Register yourself with `scripts/add_trainee.py` (Flow 5), or the bot answers you with that too.
- **Dedup is the unique `external_id` column** (WhatsApp's wamid, or `tg:<chat>:<message>`). Meta and Telegram sometimes deliver the same message twice; the second insert fails and is skipped, so the bot doesn't answer twice.
- **The reply runs after the HTTP response.** Meta wants a fast 200; an OpenAI call can take seconds. In production this becomes a Cloud Task (that's why `reply()` takes a message ID: it's what a task would carry).
- **Saving comes before sending.** The trainee only gets a confirmation for what is actually in the db.
- **Errors never reach the trainee as a stack trace.** A failed LLM call or save sends `APOLOGY`; a failed send is only logged.

`GET /webhook` (`verify_webhook()`) is separate: Meta calls it once when the Callback URL is set, and the bot answers with `hub.challenge` if `hub.verify_token` matches `WHATSAPP_VERIFY_TOKEN`. Without `WHATSAPP_APP_SECRET` / `WHATSAPP_VERIFY_TOKEN` both WhatsApp endpoints refuse everything (anyone could sign with an empty secret). `GET /` is a plain health check (open `<tunnel url>/` in a browser to see that the bot is reachable).

## Flow 1b: a trainee writes on Telegram

Telegram doesn't send the phone number, so the first thing in a new chat is connecting it to the trainee the trainer registered (by phone).

```
Telegram ──POST /telegram/webhook──► main.receive_telegram()
  ├─ telegram.verify_secret(settings, X-Telegram-Bot-Api-Secret-Token)    wrong / no token → 401
  │     the secret is telegram.webhook_secret(): an HMAC of the bot token, given to Telegram by setWebhook
  ├─ telegram.parse_update(update) → InboundMessage(external_id "tg:<chat>:<id>", chat_id, user_id,
  │                                                 private, type, text, contact_phone, contact_user_id,
  │                                                 voice_file_id, voice_duration)
  │     text: a text message's text, or a photo's (video's, ...) caption
  │     not a message (an edit, ...) or not a private chat (a group) → 200, nothing else
  ├─ trainees.find_by_telegram(chat_id)              → Trainee or None
  ├─ None, and the message is the sender's own number (contact.user_id == from.id):
  │     trainees.link_telegram(phone, chat_id)  normalize the number, find the trainee by it,
  │                                             trainee.telegram_chat_id = chat_id
  ├─ a trainee's voice message: transcribed before it's stored (below)
  │     external_id already stored (redelivered) → 200, not transcribed again
  │     voice.text_of(settings, external_id, file_id, duration)   in a thread (asyncio.to_thread)
  │       longer than VOICE_MAX_SECONDS (300) → None
  │       telegram.download_file(file_id)   getFile, then api.telegram.org/file/bot<token>/<path>
  │       voice.transcribe(audio)           OpenAI OPENAI_TRANSCRIBE_MODEL, language "pl"
  │       a failure or nothing recognised → None (logged)
  │     text → the message's text (after the caption, if any); None → VOICE_NOT_UNDERSTOOD below
  ├─ INSERT Message(channel="telegram", external_id, ...)
  │     a (now) known trainee: with body, phone, trainee_id, trainer_id
  │     anyone else:           only what dedup needs
  │     external_id already there (Telegram redelivered) → rollback, 200
  └─ then (main.after_storing(): after the 200 locally, before it with REPLY_IN_REQUEST on Cloud Run):
        just connected      → send_telegram_notice(TELEGRAM_LINKED, REMOVE_KEYBOARD, trainee)  stored
        unknown, own number → send_telegram_notice(UNREGISTERED, ASK_FOR_PHONE)                not stored
        unknown, anything else (/start, text, someone else's contact card)
                            → send_telegram_notice(TELEGRAM_ASK_FOR_PHONE, ASK_FOR_PHONE)       not stored
        trainee, "/start"   → send_telegram_notice(TELEGRAM_HELP, trainee)                     stored
        trainee, voice message not transcribed → send_telegram_notice(VOICE_NOT_UNDERSTOOD, trainee)  stored
        trainee, "yanek" or a transcribed voice message (conversation.calls_bot())
                            → main.reply(message.id)   the same as in Flow 1; the reply goes to Telegram
        trainee, otherwise  → nothing; the message waits for the next "yanek"
  the shared number, "/start" and a voice message that wasn't transcribed are stored handled
  (answered by the fixed text, not reports), so the next reply reads them as context
```

- **`ASK_FOR_PHONE`** is a keyboard with one button, "Udostępnij numer telefonu" (`request_contact`). Pressing it sends the trainee's own number, which Telegram has verified; a contact card from the address book has another `user_id` and connects nobody.
- **A new Telegram account** (another phone, a reinstall): sharing the number again moves the trainee to the new chat; the old chat is then a stranger's.
- **What the bot told a trainee is history**: the welcome and the help are stored as bot messages, so the model sees "[wiadomość typu contact]" followed by the welcome, then the reports. Texts to strangers aren't stored, like `UNREGISTERED` on WhatsApp.
- **Formatting**: the model writes WhatsApp's `*bold*` / `_italic_` (the prompt asks for chat formatting), which is Telegram's legacy Markdown too, so replies go with `parse_mode=Markdown`. If Telegram can't parse it (a stray `*` or `_`), `telegram.send_text()` sends the text as it is.
- **The bot token is in every API URL** (`api.telegram.org/bot<token>/sendMessage`). `telegram._call()` raises `TelegramError` with Telegram's own description and hides httpx's exception (which carries the URL); httpx's request logging is off. A failed send is logged as e.g. `sendMessage: 403 Forbidden: bot was blocked by the user`.
- **Voice messages** need no "yanek": they're sent to the bot only, and nobody says the name into one. Telegram doesn't transcribe for bots (only Premium users, in the app), so `voice.py` downloads the recording and OpenAI transcribes it; the transcript is the stored text, so from then on a voice message is read exactly like a typed one (history, the dashboard, corrections). `called_again()` counts a later voice message as calling the bot too. The model sees it as "[wiadomość głosowa, transkrypcja] …". It's transcribed in the webhook, before storing, so no reply ever reads a voice message without its text; that makes the webhook take a few seconds for a voice message. The audio isn't kept.
- **One history across channels**: `recent_history()` reads the trainee's messages whatever their channel, so a trainee who moves from WhatsApp to Telegram keeps the context.

## Flow 1c: on Cloud Run

The same webhooks, with two settings on (docs/DEPLOY.md):

```
REPLY_IN_REQUEST=true   main.after_storing(): the reply (or fixed text) runs in a thread inside the
                        request (asyncio.to_thread), not as a background task after the 200;
                        Cloud Run gives the container CPU only while a request runs
DB_BUCKET=yanek-bot-db  main.keep_database_in_bucket() (middleware), after every request:
                          db_sync.upload_if_changed(): the database or its -wal file changed
                          since the last upload → snapshot() (SQLite's backup API) → upload()
                          to gs://<bucket>/wabot.db; a failure is logged and retried next time
deploy/start.sh at start: deploy/fetch_db.py (gs://<bucket>/wabot.db, else seed/wabot.db, else
                          nothing) → alembic upgrade head → uvicorn
```

## Flow 2: the LLM call

What happens inside `assistant.generate_reply(settings, trainee, context, new, now)` (`context` and `new` from `conversation.load_turn()`, Flow 1):

```
generate_reply()
  ├─ plan = plans.of(trainee)              the plan of the version trainees.plan_version_id
  │                                          points to (loaded with the trainee) → TrainingPlan
  ├─ build_instructions(plan, trainee.name)  → the system prompt (one string):
  │     PROMPTS["system"]                     who Yanek is, all extraction and reply rules
  │   + PROMPTS["trainee_name"]               "…ma na imię Kasia." (zrobiłaś / zrobiłeś)
  │   + PROMPTS["training_plan"]              with plan.prompt_text() filled in (below)
  │     (or PROMPTS["no_training_plan"] when the trainee has no plan)
  ├─ reply_input(settings, context, new)  → the chat turns, shaped by HISTORY_MODE:
  │     new = from the first message still waiting (the report being sent now)
  │     HISTORY_MODE=marked (default):  context + developer "--- NOWE WIADOMOŚCI ---" + new
  │     HISTORY_MODE=new_only:          new only (the code drops the context)
  │     no context (nothing handled in the window): everything is new, no marker
  │   to_llm_messages() turns stored messages into [{"role": "user"|"assistant", "content"}]:
  │     "in" → user, "out" → assistant; a voice message → "[wiadomość głosowa, transkrypcja]"
  │     + the transcript; other non-text → "[wiadomość typu image]", with its
  │     caption after it if it has one ("[wiadomość typu photo] yanek 80,4");
  │     bot messages before the trainee's first one (a reminder, a message the bot started)
  │     become one developer note first ("Wcześniej … napisałeś do podopiecznego:" + the
  │     messages, prompts.yaml `earlier_bot_messages`), so the model knows what's answered
  ├─ + {"role": "developer", "content": _today(trainee, now)}
  │     "Dzisiaj jest wtorek, 2026-11-03, godz. 10:15." in the trainee's timezone
  └─ _complete(settings, instructions, messages, today, plan)
       ├─ no OPENAI_API_KEY → MissingApiKeyError
       └─ up to ATTEMPTS (2) times:
            ├─ client.responses.create(model=OPENAI_MODEL, instructions, input=messages,
            │       text.format = strict json_schema from llm_output.json_schema(),
            │       reasoning effort low, store=False; client timeout 60 s, one retry)
            ├─ a "refusal" part in the output → return LlmOutput(reply=REFUSAL)
            ├─ empty output_text → RuntimeError
            ├─ llm_output.validate_output(raw, today, plan, allowed_letters)   → LlmOutput
            │       (see below; allowed_letters: the instructions and messages sent)
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
2. `reply`: not empty, not JSON, at most 4096 characters (WhatsApp's limit), only Latin letters (Polish letters and emoji are fine; Cyrillic or Armenian slip-ins are not), except letters that are in the instructions or the messages sent: a Ukrainian trainee's name, or their own words.
3. `weight`: 30–300 kg, date not in the future and not more than 31 days ago.
4. `training`: same date window; `skipped` has no exercises, `done`/`partial` have at least one; sets 1–20; load 0–500 kg; a `replaces` exercise has `in_plan: false`.
5. Against the plan (`_check_against_plan()`): `plan_day` must be one of the plan's days (case and spacing don't matter; it's set to the plan's exact name). An `in_plan` exercise needs the ID of an exercise planned for that day. A planned ID with `in_plan: false` is wrong unless it replaces something. `replaces` must be an ID planned for that day. Without `plan_day`, any day of the plan counts. Without a plan, `plan_day`, `in_plan` and `replaces` must all be empty.
6. Everything passed, so the code fills in and fixes what it can itself. `**bold**` (Markdown) becomes `*bold*` (WhatsApp) in the reply. `_fill_in_missed()` sets `missed_exercises` to the plan day's exercises that aren't in the report as done (`in_plan`) or replaced (`replaces`): what the trainee didn't report counts as not done. It then sets the status to `done` if nothing is missed, otherwise `partial`. `skipped` stays, with the whole day missed. Without a plan day, nothing is missed. `_match_to_catalog()` gives every exercise that isn't in the plan or its replacements the catalog ID matching its name ("Pompki" → `push_up`), keeps the model's own ID if the name matches nothing, and leaves it null if there's neither. Then the `LlmOutput` is returned.

## Flow 2b: saving the extracted data

`entries.save(session, trainee, message, output, daily_updates_only, now)`, called from `main.save_report()` with `DAILY_UPDATES_ONLY` from `.env` and `now=utcnow()` (the simulation's time when replaying, so simulated entries are dated like their messages). A new row gets `created_at` and `updated_at` = `now`, an updated one `updated_at`:

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
  │     kind = outside_plan(training): no plan day, not skipped, nothing from the plan
  │       (a run); False for the plan's training, also plan exercises without a known day
  │     a TrainingCheckin for training.date, the same way, but with DAILY_UPDATES_ONLY
  │       the newest one of the same kind: a run doesn't replace the day's plan training
  │     set outside_plan = kind
  │     set plan_version_id = trainee.plan_version_id: the version the reply was checked
  │       against (main.reply() read the trainee, with its version, before the LLM call;
  │       a plan changed meanwhile doesn't change it); null without a plan
  │     set plan_day_id: the ID of the day named plan_day in that version (null outside
  │       the plan), the same ID before and after the trainer renames the day
  │     set plan_day, status, missed_exercises, notes, message_id
  │     checkin.exercises = [TrainingExercise(**ex) for each exercise]
  │         on an updated check-in, the old exercise rows are deleted (delete-orphan)
  └─ COMMIT   (SQLAlchemy sends the INSERT or UPDATE here)
```

- **By default nothing is overwritten.** "yanek 80" then "yanek sorki, 81" leaves two rows for the day, 80 and 81. The current value of a day is its newest row (highest `id`); that's what the dashboard should show, and the older rows are the history of corrections. A corrected training is likewise a whole new check-in with its own exercises.
- **`DAILY_UPDATES_ONLY=True`** keeps one row per trainee per day: the same two messages leave one row, 81. If a day already has several rows (saved while the setting was off), the newest one is updated.
- **Two kinds of training a day.** For trainings "the newest row" is per kind: the plan's training and one outside the plan (`outside_plan`) are both current, so a morning day A and an evening run are two trainings. A report of the same kind is a correction, also with another plan day ("sorki, to był B") or after plan exercises saved without a day (the bot asked which day it was), because the bot repeats the day it understood so the trainee can correct it.
- **The "newer message" check** (only with `DAILY_UPDATES_ONLY=True`) is for replies that finish out of order: an older report never overwrites a newer one. By default both are kept anyway, and the one from the newer message has the higher `message_id`.
- Messages sent close together ("yanek 80", "yanek sorki, 81" before the first reply is out) still give one row: `called_again()` in Flow 1 skips the first (before or after its LLM call), and the second's turn has both as new and returns 81. The 80 stays in `messages`.
- `trainer_id` is copied from the trainee onto every entry, so the dashboard can filter every query by trainer.

## Flow 3: the bot starts a conversation itself

Only the script `scripts/send_test_custom_first_message.py` does this so far; later the scheduler will (reminders). The message goes to the active channel (`CHANNEL`); on Telegram the trainee must have connected first (the script says so), and WhatsApp's 24-hour window doesn't apply.

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
  ├─ WhatsApp, no inbound WhatsApp message in 24 h → warning (WhatsApp will probably drop it)
  └─ conversation.send_and_store()   on CHANNEL
```

## Flow 4: `bash run.sh`

```
run.sh
  1. uv run alembic upgrade head            create/upgrade wabot.db
  2. uv run python scripts/send_test.py     the messenger works:
       telegram: getMe with the token → "bot @x works, https://t.me/x"; if WHATSAPP_TEST_RECIPIENT is a
                 connected trainee, a test message to them (otherwise how to connect)
       whatsapp: a template message to WHATSAPP_TEST_RECIPIENT
  3. (unless TUNNEL=0) scripts/start_tunnel.py starts cloudflared → .cloudflared.log, without a
     console (Windows: uvicorn's reload sends Ctrl+C to the whole console, which used to stop the
     tunnel too, and Telegram got 530); taskkill'd again on exit
  4. in the background:
       wait up to 30 s for https://xxx.trycloudflare.com in the log
       → scripts/set_webhook.py <url>           (for CHANNEL)
         telegram: telegram.set_webhook(<url>/telegram/webhook) with the secret, retried until
                   Telegram accepts it; prints the bot's link
         whatsapp:
           app ID from WHATSAPP_APP_ID or GET /app with the token
           every 5 s, for up to 2 minutes (WAIT_FOR_TUNNEL, RETRY_EVERY):
             POST /{app_id}/subscriptions with callback_url=<url>/webhook,
                  the verify token and the app token (app ID | app secret)
               → Meta immediately calls GET <url>/webhook (verify_webhook) to check it
             Meta reached the bot → print "Webhook set: <url>/webhook", done
             Meta couldn't (tunnel not live yet, bot not up) → try again
           still failing after 2 minutes → exit with Meta's error;
             run.sh then prints "WARNING: set the Callback URL by hand: <url>/webhook"
  5. uv run uvicorn wabot.main:app --reload --reload-dir src --reload-include "*.yaml"   (foreground;
     reloads only for changes in src/, not .venv or logs: a reload drops the requests in flight)
```

Steps 4 and 5 run at the same time: `set_webhook.py` first waits until the tunnel's name is in public DNS (`wait_for_dns()`, asking dns.google over HTTPS; a resolver asked too early remembers "no such name", and Telegram's then refuses the tunnel for many minutes), then keeps retrying until the bot started in step 5 answers Meta through the tunnel.

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
  └─ COMMIT, print what is saved; not on Telegram yet → how to connect (open the bot, share the number)
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

## Flow 6: a trainer opens the dashboard

The same FastAPI app as the webhook; `main.py` calls `dashboard.routes.install(app)` at import, which adds the `/dashboard` pages, the static files and the redirect to the login page.

1. **`GET /dashboard/trainees/3/weight`** → FastAPI resolves the page's dependencies first:
   - `auth.current_trainer`: reads the `wabot_session` cookie, checks its signature (key: `DASHBOARD_SESSION_SECRET`, or a fixed dev key with `DASHBOARD_DEV_LOGIN=True`) and its age (30 days), loads the `Trainer`. No cookie, a bad one, or a trainer that no longer exists: it raises `NotSignedIn`, which the handler from `install()` turns into a redirect to `/dashboard/login`.
   - `auth.trainee_of_trainer`: loads trainee 3 and answers **404** unless `trainee.trainer_id` is the signed-in trainer's. Pages get the trainer and trainee only from these two, never from the URL or a form, so a trainer can't open another trainer's trainee by changing the number.
2. The page function (`routes.weight`) calls `render()`, which adds what every page needs: the trainer's own trainees for the dropdown, the chosen trainee, the current section. The dropdown's options are the same page for each trainee (`/trainees/<id>/weight`), and `app.js` opens the chosen one.
3. The template extends `base.html` (sidebar: dropdown, Waga / Treningi / Plan treningowy, greyed out without a trainee, then Podopieczni, the trainer's name and Wyloguj). Without a trainee, hovering a trainee page shows "Wybierz podopiecznego" instead of its name, and clicking it highlights the dropdown (`app.js`).

**The messages behind an entry** (`queries.conversations()`, `templates/_conversations.html`): every weight and training on these pages has a closed "Pokaż wiadomości" (a `<details>`, no JavaScript) with the conversation of each report of that day, oldest first, the last one the one that counts ("to się liczy"; the earlier ones "zastąpione późniejszym"). A report's conversation starts from the message the entry was saved from (`message_id`, the "yanek" the reply answered): the trainee's messages the same reply marked handled share its `handled_at` (`conversation.claim()` marks them in one statement), so it's everything from the first of them to the bot's first message after the last of them (its answer), the bot's messages in between included (an apology after a failed try). Only the trainee's own messages (`trainee_id`, `trainer_id`). A voice message shows its transcript, marked "wiadomość głosowa".

**The weight page** (`routes.weight`): `queries.weigh_ins()` takes the trainee's `weight_entries`, keeps the newest row of each date (`current_per_day()`: `MAX(id)` per date, so a correction like "sorki, 81" wins), and adds the body of the message each was saved from and how many reports the day had. The template shows three tiles (last weight, change since the first weigh-in, number of days), the chart, and a table of every day (newest first, the change from the day before, the message, "poprawiona" when the day was reported more than once). The chart's points go into the page as JSON (`<script type="application/json" id="weight-data">`, escaped by Jinja's `tojson`); `static/weight_chart.js` reads it and draws the SVG: dates spaced by time, round kg gridlines around the data (not from zero), the latest value at the end of the line, and a tooltip with the day, the weight and the message for the day nearest the pointer (or the arrow keys).

**The trainings page** (`training_pages.trainings`, `GET /trainees/<id>/trainings?month=2026-10`; without `month`, or with something that isn't one or is outside 2000–2100, this month in the trainee's timezone):

1. The month's whole weeks, Monday first (`calendar.monthdatescalendar`), so the first and last rows include days of the months around it.
2. `queries.workouts(session, trainee, first, last)` for those dates: the newest check-in of each date and kind (`MAX(id)` grouped by `date, outside_plan`) with its exercises, how many reports of that kind the day had, the message it was saved from, and:
   - the **label** (`Workout.label`): its day's label in the current plan (`TrainingPlan.day_labels()`: the trainer's `PlanDay.label`, or A, B, C by position for a day without one; found by `plan_day_id`, so a renamed or moved day still matches the legend), or in the version it was saved against when the day is no longer in the plan; none outside the plan or when the day wasn't known;
   - `current_name`: the day's name in the current plan when it was renamed since;
   - `planned`: how many exercises the day had in its version, for "4/5".
3. The template (`trainings.html`): four tiles (the last training overall, then the shown month's plan trainings, skipped ones and ones outside the plan), the month bar (‹ ›; no › past this month), the calendar table (a cell per date with a mark per training: the letter over ✓, "4/5" or ✕, coloured by status, or "+" outside the plan; a tooltip and an `aria-label` say it in words; a count of the week's plan trainings at the end of each row), a legend with the current plan's days, and the month's trainings, newest first: each day an anchor (`#d-2026-10-06`) the calendar cell links to (a day of the next or previous month links to that month's page). Each training: its mark, name, status in words, "poprawiony", the missed exercises by catalog name, the exercises (`pl_dose`: "4 × 8, 62,5 kg"; "zamiast: ..." for a replacement, "spoza planu" for an extra exercise), notes, and "Pokaż wiadomości" (above; the original message is quoted with its time instead only when there's no conversation to show). The weight table does the same in its Wiadomość column.
4. A month without trainings says so and links to the last training; a trainee without any trainings gets one line saying how they appear.

**A trainee's plan** (`plan_pages.py`, `GET /trainees/<id>/plan`): `plans.of(trainee)` (the plan of the version `trainees.plan_version_id` points to) drawn by the `plan_cards` macro (`templates/_plan.html`): the notes and one card per day, a table of the exercises with their catalog names (`exercises.BY_ID`), sets, reps, weight, note and replacements. Above it a bar with the current version (`trainee.plan_version`: its name, where it came from, since when), "Przypisz" with the trainer's library plans, and "Zapisz w bibliotece". Below it "Historia planów": every version, newest first, each with "Podgląd" and "Przywróć". No plan: "Utwórz plan" or a plan from the library.

**Every change is a version.** Whatever changes a trainee's plan calls `plan_history.assign(session, trainee, plan, source, template)`: it adds a `trainee_plans` row (`source`: `edited`, `template`, `restored`, `script`, or `imported` for plans from before migrations 0006/0007) and points the trainee to it (`trainees.plan_version_id`), which is what the bot reads. The plan itself is stored once, in the version. The pointer can only name one of the trainee's own versions: a two-column foreign key, so the database refuses anything else. `plan_history.remove()` (`add_trainee.py --no-plan`) sets the pointer to null: no plan, and the versions stay in the history and can be restored. A change that changes nothing adds no version (`assign()` returns None): an edit without changes, restoring a version equal to the current plan, assigning the library plan the trainee already has ("Podopieczny ma już ten plan z biblioteki"). Without a plan nothing counts as unchanged, so saving the last plan again gives it back. An edited or restored plan keeps the name and `template_id` of the version it's based on (`based_on`).

**Day IDs.** Every stored plan day has an ID the trainer never sees (`PlanDay.id`, 8 hex digits), and it stays the same while the day is renamed, moved or edited, in later versions and in copies of a library plan. Saved trainings point to their day by it (`training_checkins.plan_day_id`), so trainings of "Dzień A" before a rename and of "Poniedziałek – nogi" after it are one day. `plans.with_day_ids(plan, earlier)` gives them: a day that has an ID keeps it; a day without one (a YAML plan, a day just added in the editor) takes the ID of the day with the same name in `earlier`, otherwise a new ID, never one an earlier day had. `assign()` passes, in this order, the library plan, the version it's based on and the trainee's earlier versions, so registering the same YAML plan again, or deleting a day and adding it again under its name, keeps the day. `plans.to_db()` gives any day still without an ID a new one, so no plan is stored without them. The LLM still answers with the day's name; the code finds the ID. `history()` sees only versions with the trainee's current trainer, and the library's "Podopieczni z tym planem" only the trainer's own trainees, as a safeguard: a trainee never moves to another trainer (`add_trainee.py --trainer` refuses for an existing trainee).

**Editing** (`GET`/`POST /trainees/<id>/plan/edit`), a separate page rather than editing in place, so the plan page stays readable and "Anuluj" just leaves:

1. `edit_trainee_plan`: `plan_editor.form_data()` turns the plan into the form's shape (every field present, a new plan is one empty day; each day with its ID, `data-day-id`, and its label, A, B, C by position for a plan without labels) and `plan_edit.html` renders it: notes, then per day a label ("Oznaczenie"), a name and the exercises (a catalog dropdown grouped by muscle group, sets, reps, weight, note, replacements as chips with "+ dodaj zamiennik"). The same macros fill `<template>` elements for a new day, exercise and chip. The same page edits library plans, with a name field on top.
2. In the browser, `static/plan_editor.js` adds, removes (a day after a confirm) and moves days and exercises with ↑ ↓, and adds replacement chips (not the exercise itself, not twice). A new day gets the first free label (A, B, C, ...). Leaving with unsaved changes asks first.
3. "Zapisz plan": the script reads the form into one JSON object shaped like `plans.TrainingPlan`, each day with its ID ("" for a day added here), and posts it as the `plan` field.
4. `save_trainee_plan` (behind the `current_trainer` / `trainee_of_trainer` guard, so another trainer's trainee is 404) calls `plan_editor.parse()`: it trims the text, treats empty fields as not filled in, and validates with `TrainingPlan`, the same rules as a YAML plan (a name per day, no two days with one name, a label of up to 3 characters and no two days with one label, at least one exercise, catalog IDs only, no exercise twice in a day, no exercise as its own replacement, sets a whole number above 0, day IDs well-formed and not twice; only a tampered form breaks those). The plan models raise their own error types (`plans._error`), which `parse()` turns into Polish with the place: "Dzień 2 „Wtorek”, ćwiczenie 3: wybierz ćwiczenie".
5. Errors: the editor again (422) with the list and what the trainer typed, nothing saved. Otherwise `plan_history.assign(..., "edited")`, commit, and a redirect to the plan page with "Zapisano plan" (`?done=saved`).

**Assigning, restoring, copying** (all POST, redirect back to the plan page with `?done=...`):

- `/plan/assign` (`template_id`): the library plan must be the trainer's (404 otherwise); a copy of it becomes the trainee's plan (`source="template"`). Asks first in the browser when the trainee has a plan (`data-confirm`, handled in `app.js`).
- `/plan/versions/<v>` shows one version (`version_of_trainee`: 404 unless it's this trainee's); `/plan/versions/<v>/restore` makes it current again as a new version (`plan_history.restore()`); nothing is deleted.
- `/plan/to-library` (`name`): the current plan copied into the library under a new name.

**The plan library** (Biblioteka planów, `/dashboard/plans`, in the sidebar under Podopieczni): the trainer's `plan_templates`, not tied to a trainee, with the trainees whose current plan came from each (`users_of()`; "(zmieniony)" when they edited it since). `/plans/new` and `/plans/<t>/edit` use the editor with a name (required, not the same as another of the trainer's library plans, any case); `/plans/<t>` shows it; `/plans/<t>/delete` removes it. Every library page goes through `template_of_trainer` (404 for another trainer's). Assigning copies, so editing or deleting a library plan never changes a trainee's plan; the trainee's history keeps the name it had (`trainee_plans.name`; `template_id` becomes null when it's deleted).

The bot reads the current version for every reply (`assistant.build_instructions(plans.of(trainee))`), so a new plan applies from the trainee's next message. Saved check-ins keep the plan day name and missed exercises they were saved with, and point to the version they were checked against (`training_checkins.plan_version_id`). So renaming a day or changing the plan later doesn't change what they mean: the calendar can read the day from that version.

**Signing in with Google** (when `GOOGLE_CLIENT_ID` is set):

1. `GET /dashboard/login` shows Google's "Zaloguj się przez Google" button (Google's script, `accounts.google.com/gsi/client`).
2. The trainer picks an account in Google's popup. Google's script then posts a form to `POST /dashboard/login/google` with `credential` (an ID token: a JWT signed by Google with the account's email) and `g_csrf_token` (the same random value is also set as a cookie; `google_login()` checks they match, so another site can't post a token here).
3. `auth.verify_google_token()` checks the token with Google's public keys (`google-auth`): signature, expiry, issued for our client ID, and `email_verified`. Invalid: the login page again with an error (401).
4. `trainees.trainer_by_email()` finds the trainer whose `trainers.email` is that email (lowercase). None: "Brak dostępu" (403). Otherwise `auth.sign_in()` sets the session cookie and redirects to `/dashboard`.

So a trainer gets access when the operator gives their trainer row their Google email (`scripts/add_trainee.py --trainer-email`). Signing in needs `DASHBOARD_SESSION_SECRET`; the login page says so when it's missing.

**Dev login** (`DASHBOARD_DEV_LOGIN=True`, local only): the login page also lists every trainer, and `POST /dashboard/login/dev` signs in as the one clicked, no Google. Without the setting, `/login/dev` answers 404. `POST /dashboard/logout` deletes the cookie.

**Data to look at locally:** `uv run python scripts/setup_db.py --simulated` adds the three scenario trainees and their trainers (emails and plan libraries included) with their month of reports to the database `DATABASE_URL` points to (the same one the bot uses); then `bash run_dashboard.sh` and open http://localhost:8001/dashboard. `scripts/simulate_month.py` (the real model, or `--canned`) writes the same into its own scratch `simulated_month.db` instead, recreated every run: `DATABASE_URL=sqlite:///simulated_month.db bash run_dashboard.sh` shows it.

## The database

`models.py`; changing a model means a new Alembic migration (see CLAUDE.md for the command).

```
trainers ──< plan_templates          (the trainer's plan library)
         ──< trainees ──< trainee_plans  (every version of the trainee's plan; trainees.plan_version_id
                     │                    points to the current one, each training_checkin to its own)
                     ──< messages
                     ──< weight_entries        (per report; the newest per date is current)
                     ──< training_checkins     (per report; the newest per date and kind is current) ──< training_exercises
```

| Table | Columns | |
|---|---|---|
| `trainers` | `id`, `name`, `email` (unique, null until set), `created_at` | created by `scripts/add_trainee.py` for now; `email` is the Google account for the dashboard |
| `trainees` | `id`, `trainer_id`, `phone` (unique), `telegram_chat_id` (unique, null until connected), `name`, `timezone`, `consent_date`, `plan_version_id` (null without a plan), `created_at` | `phone` is how a WhatsApp message finds its trainee, `telegram_chat_id` how a Telegram one does (set when the trainee shares that phone number with the bot; migration 0010); `plan_version_id` is the current plan, one of the trainee's own `trainee_plans` rows (foreign key `(id, plan_version_id)` → `(trainee_id, id)`), read with `plans.of(trainee)` |
| `plan_templates` | `id`, `trainer_id`, `name` (unique per trainer), `plan` (JSON), `created_at`, `updated_at` | the trainer's library; assigning copies `plan` into `trainee_plans` |
| `trainee_plans` | `id`, `trainer_id`, `trainee_id`, `plan` (JSON, a `plans.TrainingPlan`), `source`, `template_id` (null when not from the library, or deleted), `name`, `created_at` | written only by `plan_history.assign()`, which also moves the trainee's pointer; unique `(trainee_id, id)` for the two foreign keys that point here; migration 0006 made each plan already on a trainee its first version (`imported`), 0007 replaced the copy on the trainee with the pointer |
| `messages` | `id`, `channel` (`telegram`/`whatsapp`), `external_id` (unique), `direction` (`in`/`out`), `phone`, `type`, `body`, `trainee_id`, `trainer_id`, `created_at`, `handled_at` | chat history and webhook dedup; `handled_at`: when the bot dealt with a trainee's inbound message (a reply saved what it reported, or a fixed text answered it), null while it waits for the next "yanek" and for the bot's own messages (migration 0011, which marked older ones handled at the bot's first message after them); `external_id` is WhatsApp's wamid or `tg:<chat>:<message>`; `body` and `trainee_id` are null for people who aren't registered (and for messages from before migration 0002), `phone` too on Telegram. Migration 0010 renamed `wamid` to `external_id` and marked the older messages `whatsapp` |
| `weight_entries` | `id`, `trainer_id`, `trainee_id`, `date`, `kg`, `message_id`, `created_at`, `updated_at` | |
| `training_checkins` | `id`, `trainer_id`, `trainee_id`, `date`, `plan_version_id`, `plan_day_id`, `plan_day`, `outside_plan`, `status`, `missed_exercises` (JSON list of exercise IDs), `notes`, `message_id`, `created_at`, `updated_at` | `plan_version_id`: the trainee's plan version the report was checked against (null without a plan); `plan_day_id` the day's ID (the same across renames and versions), `plan_day` its name in that version. Migration 0007 filled `plan_version_id` for older check-ins with the version current at their `updated_at`; 0008 gave stored plan days their IDs (by name along each trainee's versions) and check-ins their `plan_day_id`. `outside_plan`: training outside the plan (a run), the other kind of a day's training; migration 0009 set it by the same rule as `entries.outside_plan()` |
| `training_exercises` | `id`, `checkin_id`, `exercise_id`, `name`, `in_plan`, `sets`, `reps`, `weight_kg`, `replaces`, `comment` | same fields as `llm_output.Exercise`; `exercise_id` is the catalog ID progress is charted by, `replaces` the ID of the replaced plan exercise |

- `message_id` on an entry is the trainee's message the bot answered when it saved the entry (the "yanek ..." one), so the dashboard can show the original message next to it.
- All timestamps are UTC (`UTCDateTime`, so SQLite and Postgres behave the same). `date` columns are the trainee's local day.
- **Reading the current value**: the newest row per trainee and date (for trainings, per trainee, date and `outside_plan`), e.g. `SELECT * FROM weight_entries w WHERE id = (SELECT MAX(id) FROM weight_entries WHERE trainee_id = w.trainee_id AND date = w.date)`. An index on `(trainee_id, date)` keeps it fast. Migration 0004 replaced the old one-per-day unique constraint with it.
- Migration 0003 turned `training_plan` from text into JSON and cleared the old free-text plans. Migration 0007 removed that column: the plan lives in `trainee_plans`, and the trainee points to it.
- `trainees` and `trainee_plans` point at each other, so the pointer's foreign key is created after both tables (`use_alter`), and `tests/conftest.py` drops the tables with foreign keys off.
- On SQLite, `alembic/env.py` turns foreign keys off while migrating. Batch mode changes a table by copying it and dropping the old one, which foreign keys would block.

To look at the data, open `wabot.db` in any SQLite browser (DB Browser for SQLite, or a SQLite extension for VS Code).

## Where to change what

| I want to... | Change |
|---|---|
| change what Yanek says or how it extracts | `src/wabot/prompts.yaml` (`system`) |
| add a field to the extracted JSON | the pydantic model in `llm_output.py`, the rules in `prompts.yaml`, a check in `validate_output()` if needed, a column in `models.py` + a migration, tests in `tests/test_llm_output.py` and `tests/test_entries.py` |
| add an exercise | a new entry in `exercises.yaml` (never rename an existing ID); plans can use it right away |
| teach the bot a nickname ("siady") | add it to the exercise's `aliases` in `exercises.yaml` |
| change a trainee's training plan | the dashboard: Plan treningowy → Edytuj plan, or Przypisz a library plan; or edit a copy of `plans/example.yaml`, then `uv run python scripts/add_trainee.py --plan path/to/plan.yaml` |
| change a trainee's plan from code | `plan_history.assign()` (or `remove()`); don't set `trainees.plan_version_id` yourself |
| add a rule for plans | a validator in `plans.py` that raises `_error("<type>", ...)`, and the Polish text for that type in `plan_editor._message()` |
| change how the plan looks to the LLM | `TrainingPlan.prompt_text()` in `plans.py`, and the `training_plan` text in `prompts.yaml` that explains the format |
| register another trainee | `uv run python scripts/add_trainee.py --phone 48... --name ...` |
| change the reply for unknown numbers | `UNREGISTERED` in `main.py` |
| switch the messenger | `CHANNEL=telegram` or `CHANNEL=whatsapp` in `.env` (its settings filled in), restart `run.sh`; trainees then write to that messenger. Nothing to migrate |
| change what the Telegram bot says before the LLM is involved | `TELEGRAM_ASK_FOR_PHONE`, `TELEGRAM_HELP`, `TELEGRAM_LINKED` in `main.py`; the button's text in `telegram.ASK_FOR_PHONE` |
| change the trigger word | `BOT_NAME` in `conversation.py` |
| change how much history the model sees | `HISTORY_WINDOW` / `HISTORY_LIMIT` in `conversation.py` |
| change the model | `OPENAI_MODEL` in `.env` (default in `config.py`) |
| see why the bot answered as it did | `LOG_LEVEL=DEBUG` in `.env`, restart, read `logs/wabot.log`: per message `Answering … plan: …`, the model's instructions and messages, its raw answer, what was saved, the answer sent |
| change what gets logged, or the rotation | `log.info()` / `log.debug()` lines in `main.py` and `assistant.py`; `MAX_BYTES`, `BACKUPS`, `QUIET` in `logs.py` |
| change the retry count | `ATTEMPTS` in `assistant.py` |
| change plausibility limits | constants at the top of `llm_output.py` |
| look at the dashboard with data | `uv run python scripts/setup_db.py --simulated` once, then `bash run_dashboard.sh` (Flow 6) |
| start from an empty database | `uv run python scripts/setup_db.py --new dev.db`, put the `DATABASE_URL` line it prints into `.env`, restart `run.sh` / `run_dashboard.sh` |
| let a trainer sign in with Google | `uv run python scripts/add_trainee.py --phone <a trainee of theirs> --trainer-email them@gmail.com`; `GOOGLE_CLIENT_ID` and `DASHBOARD_SESSION_SECRET` in `.env` (README) |
| add a column to trainers or trainees | the model + a migration, and a value for the simulated people in `scenarios/*.yaml` / `simulation.register()` (a test checks none is empty) |
| change what the training calendar shows | `queries.Workout` (the mark, the words, the letter), `templates/trainings.html`, the `.mark` / `.calendar` styles and the status colours (`--done`, `--partial`, `--skipped`, `--outside`) in `app.css` |
| add a dashboard page | a function in `dashboard/routes.py` (or its own module with a router, like `training_pages.py`, added in `install()`) that depends on `TrainerDep` (and `TraineeDep` for a trainee page) and calls `render()`, a template extending `base.html`, an entry in `TRAINEE_SECTIONS` for the sidebar, a test in `tests/test_dashboard.py` |
| see how the real model handles a month of reports | `uv run python scripts/simulate_month.py` (`--only tomek` for one trainee): the 45 messages of `scenarios/*.yaml` in time order through `main.reply()` at simulated times, into `simulated_month.db` (never `wabot.db`; built by the migrations, so its tables are the real ones), nothing sent to WhatsApp; prints each reply and what was saved, then each trainee's current values per day |
| add a simulated trainee or message | a new `scenarios/<name>.yaml` (or a report in one): `text`, the answer a correct model gives, and `saved` where the code must change it; the month test checks it and the script sends it |

## Not built yet

- **Corrections of older reports.** The LLM doesn't see what's saved, only the chat history. A correction of a training whose report is no longer in the history (older than 24 h or 20 messages, or always with `HISTORY_MODE=new_only`) replaces that day with just the corrected part. Milestone 4: show the LLM the saved entries of the last days.
- **Diet reports** are only acknowledged, not extracted or saved.
- The dashboard has the trainee list, the weight chart, the training calendar, the plan with its editor and history, and the plan library. The calendar has no expected sessions yet ("A planned, no report", "2/3 this week"): the plan fields `weekdays` and `sessions_per_week` aren't built. Trainers can't add their own exercises to the catalog yet. Google sign-in is built but untested against real Google until there's an OAuth client ID. No reminders, no photos. The dashboard doesn't show whether a trainee has connected on Telegram yet.

## Tests

`uv run pytest`. `tests/conftest.py` points the app at a temporary SQLite db (tables recreated for every test) and sets fake tokens and keys as environment variables. It turns `.env` off for tests, so they don't depend on your local settings: only those values and the code's defaults apply. No test calls WhatsApp or OpenAI.

- `test_logs.py`: the log file rotating (and keeping only `BACKUPS` old files), the level filtering, DEBUG not turning on httpx (the token), setup twice not doubling lines, the app writing `logs/wabot.log` by default. Other tests write no file (`LOG_FILE=""` in `conftest.py`).
- `test_telegram.py`: Flow 1b: the secret (refused without it, or without a bot token), a new chat asked for the number, the own number connecting the trainee (with a "+"), someone else's contact card and an unregistered number connecting nobody, a new Telegram account taking over, `/start` again, redelivery, groups and edits ignored, a photo stored without text, a photo's caption answered like a text, voice messages (transcribed and answered without "yanek", the caption first, too long or failed or silent → the fixed reply and handled, a redelivery transcribed once, a stranger's not transcribed, answering a waiting report with it, parsing, downloading never showing the token, how the model sees it), `/start` and the shared number not hiding a waiting report, one history across WhatsApp and Telegram with each reply going back where its message came from, a failed send logged; the client: the request it sends, errors never showing the token (also in the traceback), httpx's logging off, the formatting fallback, the derived secret, parsing; `missing_for_channel()`, `scripts/set_webhook.py` on Telegram, WhatsApp refusing everything without its secrets. `tests/conftest.py` makes any Telegram API call in a test fail (`no_telegram`).
- `test_webhook.py`: Flow 1 through FastAPI's test client, with the LLM and `send_text` replaced by fakes: replies, dedup, unregistered numbers, saving, corrections, a failed save (the message keeps waiting), a stored plan that no longer validates (the apology), an image's caption as its text, a message followed up on (no LLM call for it); what "handled" fixes: after a failed send the report isn't saved again, after an apology it's read again, messages another reply took meanwhile are read again. Its `add_trainee()` helper is used by the other test files.
- `test_entries.py`: Flow 2b: one entry per day, corrections, out-of-order saves, exercises replaced, a check-in's plan version (the new one after a plan change, none without a plan), the database refusing another trainee's version, trainings before and after a day is renamed having the same day ID; the two kinds of training (what counts as outside the plan; a run on a plan day is a second training and its correction changes only the run; another plan day on the same date, or the day after "który to był dzień?", is a correction), in both `DAILY_UPDATES_ONLY` modes.
- `test_conversation.py`: the name trigger, history per trainee, `called_again()`, the turn (new from the first waiting message, a reminder before it as context), the marker between context and new, plan injection and the plan text, bad plans, the example plan, `exercises.lookup()`, the trainee's local date, `scripts/add_trainee.py`.
- `test_month_scenario.py`: the three trainees of `scenarios/` (15 reports each in October 2026, three plans, two trainers), interleaved in time order through the whole reply flow (`simulation.Replay` → `main.reply()` → `_complete()` with a fake OpenAI client returning the scenario's answer → `validate_output()` → `entries.save()`). After every message it checks that the model got only that trainee's messages and the simulated date, and what was saved: weight, plan day, status, missed exercises, exercise IDs and replacements. Runs in both `DAILY_UPDATES_ONLY` modes. Some answers say `done` where the code must make it `partial`. Kasia runs in the morning of a day B: both are that day's trainings.
- `test_dashboard.py`: Flow 6: pages redirect to the login page without a session, the dev login only with `DASHBOARD_DEV_LOGIN`, a trainer sees only their own trainees (404 on another trainer's, also absent from the dropdown), the dropdown keeps the page, tampered or wrongly signed cookies, logout; the weight page (the newest report per day, the message, Ola's data not on Kasia's page, a message can't break out of the JSON script tag, no weigh-ins), the "Wybierz podopiecznego" hint, the Polish date, month, kg and dose formats; the trainings page (the month's cells and their marks, a run beside a plan day, the correction counts, the week counts, the details with missed exercises, replacements and messages, another trainee's trainings absent; a day's label follows the current plan by the day ID; months: days of the next and previous month, this month by default and for a bad or out-of-range `month`, no month after this one, a link to the last training; no trainings); the plan pages (every change a version and an unchanged save none, preview and restore, another trainee's version is 404, library plans created with a name of their own, assigning copies so library edits and deletes don't change the trainee, an edit keeps the library link, copying a trainee's plan to the library, another trainer's library is 404 everywhere), the plan editor (the plan shown with catalog names, a new plan, saving trims and drops empty fields, every rule refused with its Polish message and nothing saved, every day gets a label and it moves with its day, the typed values shown again, broken JSON is an error and not a crash, another trainer's plan is 404, the bot's prompt has the edited plan); Google sign-in with Google's token check faked: the trainer found by email (any case), refused for an unknown email, an unverified email or a forged token, the CSRF check, off without `GOOGLE_CLIENT_ID`.
- `test_schema.py`: the migrations build exactly the models' schema (`alembic upgrade head` on an empty db, compared like `alembic check`), every migration can be undone and redone, migration 0007 on rows in the old shape (the pointer to the version with the same plan, a new version where the copy differed, no pointer without a plan, check-ins linked by time, the copies back on downgrade), migration 0008 (a library copy takes the library plan's day IDs, the next version keeps them by name, a renamed or new day gets a new ID, check-ins get their day's, downgrading removes them), migration 0009 (only a check-in without a plan day, not skipped and with nothing from the plan is outside the plan; the exercises survive), migration 0010 (older messages become WhatsApp ones with their wamid as `external_id`, still unique; a Telegram message without a number; a chat ID only once and bigger than 32 bits; downgrading back), migration 0011 (a trainee's message is handled at the bot's first message after it; after the last one, and from strangers, waiting), and no exercise ID disappears from the catalog (`tests/catalog_ids.txt` lists every ID ever used; a new ID goes at its end).
- `test_llm_output.py`: `validate_output()` accepting and rejecting outputs (incl. the checks against the plan's IDs and catalog matching by name), the retry in `_complete()` with a fake OpenAI client, the schema.
