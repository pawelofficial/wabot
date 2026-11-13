# Refactor ideas – technical debt review (2026-11-13)

How this was checked: I read every module, template, script, migration and doc, and ran the test suite (264 pass), `ruff check` and `ruff format --check` (both clean) and `alembic check` (no drift between the models and the migrations). I reproduced the suspected bugs with probe tests on a temporary database (the project's own test fixtures, run from a scratch folder). `wabot.db` and `simulated_month.db` weren't opened, OpenAI wasn't called, and no project file but this one was changed.

This replaces the review of 2026-11-04 (it's in git history). What it left open was checked again: still-open items are carried over below with their old number, e.g. "(2026-11-04: 1.2)", and fixed ones are listed at the end.

**Verified** means I reproduced it. The other findings come from reading the code.

Priorities:
- **P1** – loses or duplicates trainees' data now, or shows one trainer another trainer's data.
- **P2** – fix before the feature that builds on it, or before production.
- **P3** – cleanup.

## Top of the list

| # | Problem | Priority |
|---|---|---|
| 1.1 | "Already handled" is guessed from the bot's last message: a failed send duplicates a report, an apology loses one, and on Telegram so does a `/start` in between – verified | ✅ fixed (2026-11-13) |
| 1.2 | One reply saves at most one weight and one training: "sobota A, niedziela B" keeps one of them | P2 (milestone 4) |
| 1.3 | Photos and voice messages get no answer, and "yanek 80,4" as a photo caption is dropped – verified | P2; captions ✅ fixed |
| 1.4 | A stored plan that no longer validates: no reply at all, not even the apology (came in with the logging change) – verified | ✅ fixed |
| 2.1 | Local Telegram depends on the tunnel and Telegram's DNS (today's missing answers); polling needs neither | P2 (development) |
| 2.2 | A number registered without the country code can never connect on Telegram | P2 (with "Dodaj podopiecznego") |
| 3.1 | Editing a plan in two tabs drops the other change and links the version to the wrong library plan – verified | P3 |
| 4 | Production: background replies on Cloud Run, sync database work in async webhooks, HTTPS behind the proxy, sessions, Postgres, logs with health data | P2 (milestone 7) |

---

## 1. The bot's conversation flow

### 1.1 "Already handled" is guessed from the bot's last message (verified, P1) – ✅ fixed (2026-11-13)

**Built:** `messages.handled_at` (migration 0011, older messages marked by the old rule) and `conversation.Turn`: a reply reads the messages still waiting as new and marks them handled in the save's transaction. A failed LLM call or save leaves them waiting, a failed send leaves them handled, `/start` and the shared number are handled at once. Each failure mode below has a test (`test_webhook.py`, `test_telegram.py`). The design and what's left for milestone 4 are in PLAN.md, "How a reply reads the conversation". Still open from the fix below: storing failed sends with a status, and the safety-net sweep for replies lost in a restart (section 4).

What it was:

Unchanged since the last review. The marker "--- NOWE WIADOMOŚCI ---", and with it "extract only what's new", goes after the bot's last stored message (`assistant.split_at_last_bot_message()`), so:

- **A reply that fails to send duplicates the report.** Nothing is stored, so the report is below the marker again on the next "yanek" and saved twice.
- **An apology loses the report.** The apology is stored as a bot message, the report moves above the marker, and the prompt says everything above was already saved.
- **New on Telegram: every fixed text the bot stores moves the marker too.** The help after `/start` and the welcome after connecting are stored as bot messages (so the model knows what it told the trainee).
  - **Probe:** "waga 80,4" (no "yanek"), then "/start", then "yanek zapisz". The model got:
    ```
    user      | waga 80,4
    user      | /start
    assistant | Wysyłaj mi raporty, zaczynając wiadomość od „yanek” …
    developer | --- NOWE WIADOMOŚCI --- …
    user      | yanek zapisz
    ```
    The weight is above the marker, so the model is told it's already saved, and it's lost.

**Fix (as before):** an explicit state. Inbound messages get `handled_at` (set when the reply that covered them was saved and sent); the marker goes after the last *handled* message, so failed sends, apologies and fixed texts don't move it. It's also the base milestone 4 (corrections) needs. Recommended before milestone 4.

### 1.2 One reply saves at most one weight and one training (P2, milestone 4)

`LlmOutput` has `weight: Weight | None` and `training: Training | None`. A trainee catching up after a weekend ("w sobotę A, w niedzielę B", "pon 80,2, wt 79,9") reports two days, and only one of them can be saved. The prompt doesn't say what to do with the rest, so the model either drops one without a word or, worse, says "zapisałem oba". The database already takes any number of rows per day; only the schema limits it.

- Not verified with the real model (no OpenAI calls in this review). A weekend catch-up in one of `scenarios/*.yaml` would show what it does.
- **Fix:** `weights: list[Weight]` and `trainings: list[Training]` in `llm_output.py`, each item validated as now, and `entries.save()` looping over them. Until then, a prompt rule: save the newest day and ask the trainee to send the others separately.

### 1.3 Photos, voice messages and captions get no answer (verified, P2) – ✅ captions fixed (2026-11-13)

`telegram.parse_update()` and `whatsapp.parse_messages()` keep text only for text messages. A photo's caption is dropped, so "yanek" in it isn't seen.

- **Probe:** a linked trainee sends a photo with the caption "yanek waga 80,4". It's stored as `photo` with no text, the LLM isn't called, nothing is sent.
- **PLAN.md** ("What the bot handles" → Photo) says the MVP replies that photos aren't supported yet and processes the caption like a normal message. That isn't built.
- **Why it matters on Telegram:** a photo of the scale with "yanek 80,4" and voice messages are what people actually send. Today they get silence, which looks like the bot is broken.
- **Fix:** the caption is the text (Telegram `message.caption`; WhatsApp `image.caption`, `video.caption`, `document.caption`), so the rest of the flow works unchanged. A non-text message that has no caption gets a fixed reply ("Na razie czytam tylko tekst – napisz proszę raport"): every voice message from a trainee, and a photo when the bot was mentioned recently. In the history, `to_llm_messages()` can show "[zdjęcie] <caption>".

### 1.4 A plan that no longer validates: no reply at all (verified, P2) – ✅ fixed (2026-11-13)

`main.reply()` now calls `plans.of(trainee)` for the "Answering … plan: …" log line, and that's before the `try`. Before, the first `plans.of()` was inside `generate_reply()`, inside the `try`, so the trainee got the apology and the error was logged as "LLM call … failed".

- **Probe:** a plan version with an exercise ID that isn't in the catalog. `reply()` raises `ValidationError`, nothing is sent, and the error is only in uvicorn's traceback.
- **When it can happen:** a catalog ID removed (`tests/test_schema.py` guards against that), or a new rule in `plans.py` that older stored plans break (e.g. a shorter label limit).
- **Fix:** log `trainee.plan_version_id` without validating the plan, or catch the error in the log line's summary; plus a test.

### 1.5 Smaller

- **Lists in plan answers on Telegram (P3, not verified).** The prompt asks for the plan as one exercise per line with bold day names. Telegram's legacy Markdown has no lists. A model that writes "* Przysiad …" bullets pairs those asterisks: two bullets make bold text across lines, and an odd number fails parsing, so the whole message goes out with literal asterisks. "Jaki jest mój plan?" is exactly this case. One real plan answer in `wabot.db` (2026-11-03) had no bullets (bold day name, one exercise per line), so it may never happen; if bullets show up in the DEBUG log, turn "* " at the start of a line into "• " in `llm_output._to_whatsapp_format()`.
- **Silence for messages without "yanek" (product, P3).** In a private chat, "waga 80" gets nothing back. That's the design (several messages, then "yanek, zapisz"), but a new trainee will think the bot is broken. Telegram can react to a message (`setMessageReaction`, e.g. ✍), a cheap "saved, waiting for yanek" sign. Decide together with the trigger spelling below.
- **Still open (2026-11-04: 1.6):** the trigger is Y-only ("Janek" doesn't work); first messages are validated against the whole report schema; logs contain health data. There's more of the latter now: INFO lines carry trainees' names, DEBUG the texts and the whole prompt. That's fine locally (`logs/` is gitignored), but it's P2 for production.

---

## 2. Telegram and local development

### 2.1 Local Telegram depends on the tunnel and Telegram's DNS (P2, development)

Today's missing answers: Telegram refused every `setWebhook` for the new tunnel ("Failed to resolve host"), and the messages waited at Telegram. `set_webhook.py` now waits until the name is in public DNS before registering. That's not verified yet: the next `run.sh` start shows whether it's enough.

More robust for local work: **long polling**. A small script calls `getUpdates` and posts each update to `http://localhost:8000/telegram/webhook` with the secret header. That's the same code path as the webhook, with no tunnel, no DNS and nothing to register. Telegram allows either a webhook or `getUpdates`, so the script calls `deleteWebhook` first. `run.sh` would use it on Telegram and keep the tunnel for WhatsApp; production keeps the webhook.

### 2.2 Numbers without the country code never match (P2 with "Dodaj podopiecznego") – still open (2026-11-04: 3.5)

`normalize_phone("792 541 888")` returns `"792541888"` and accepts it. Telegram always sends the whole number (`48792541888`), so a trainee registered like that can never connect: every press of the button answers "Ten numer nie jest zarejestrowany". The same goes for "0048…".

Today only `add_trainee.py` registers trainees, and its help says "with country code". The dashboard's form will make it common.

**Fix:** in `normalize_phone()`, put 48 before 9 digits and drop a leading 00 (or require the "+" form). Separately, as noted before, registering a number another trainer already has must fail without saying whose it is.

### 2.3 Smaller

- **`WHATSAPP_TEST_RECIPIENT` is "your number" on both channels.** You hit it today ("No number: pass --phone or set WHATSAPP_TEST_RECIPIENT", while on Telegram). Rename it to `TEST_PHONE`, accepting the old name too (pydantic `validation_alias=AliasChoices(...)`). P3.
- ✅ fixed: **`send_test_custom_first_message.py --to "+48 …"`** doesn't normalize the number, unlike `add_trainee.py` and `send_test.py`, so it says the number "isn't a registered trainee". P3.
- **The dashboard doesn't show who has connected on Telegram.** CODE_EXPLANATION lists this under "Not built yet". With the share-your-number step it's the first thing a trainer needs ("why doesn't Ania get answers?"): a column on Podopieczni. P3.

---

## 3. Dashboard

### 3.1 Editing a plan in two tabs drops the other change (verified, P3)

- **Probe:** tab 1 opens the editor on plan "Dzień A". Tab 2 assigns the library plan "Biblioteczny". Tab 1 saves with one number changed.
  ```
  version 1 script   | template None None          | days ['Dzień A']
  version 2 template | template 1 'Biblioteczny'   | days ['Full body']
  version 3 edited   | template 1 'Biblioteczny'   | days ['Dzień A']
  ```
  The library plan is replaced without a word. The history calls the result "Biblioteczny (edycja)", and the library lists the trainee as using it, though the content is the old plan.
- **Cause:** `assign(..., "edited")` takes `based_on` from the version that's current when the form is saved, not from the one the editor was opened on.
- **Fix:** the editor posts the version it started from (a hidden field). If that version isn't current any more, show the editor again with "Plan zmienił się w międzyczasie" and the typed values.

### 3.2 `?month=` out of range: 500 (verified, P3) – ✅ fixed

`?month=0001-01` and `?month=9999-12` raise ValueError (year 0 / 10000) in `shift()` and the calendar. Only by typing the URL. **Fix:** `month_asked()` treats months outside e.g. 2000–2100 like any bad value.

### 3.3 Still open (2026-11-04)

- **3.1, sign-in in production (P2, production):** HTTPS behind Cloud Run's proxy (`--proxy-headers --forwarded-allow-ips`, or take the scheme from config); sessions can't be revoked (30 days, only the trainer's existence is checked); no Origin check on POSTs.
- **2.4 (P3):** library plan names are case-insensitive only for A–Z on SQLite ("Łydki" and "łydki" can both exist), and two saves of one name at once answer 500 (IntegrityError).
- **2.7:** the catalog is a type built at import (`ExerciseId = Literal[IDS]`); decide how trainers' own exercises fit before building Ćwiczenia.
- **3.3 (P3):** `render()` and the Jinja filters live in `routes.py`, so `install()` imports the other routers inside the function. Move them to `dashboard/pages.py`.
- **3.4 (P3):** `assign_library_plan` repeats `template_of_trainer`'s check inline; trainers see "skrypt add_trainee" as a source; `?done=` notices come back on reload; two full catalog dropdowns per exercise row; no size limits on the posted plan.

### 3.4 Small new ones (P3)

- **Google's keys aren't cached.** `auth.verify_google_token()`'s comment says they are, but google-auth caches them only with a `CacheControl` session, so every sign-in fetches them. That's fine at this scale; fix the comment, or wrap the session. A network error there (`TransportError`) isn't caught either: 500 instead of "Nie udało się zalogować".
- **Static files have no version in their URL** (`app.js`, `plan_editor.js`, `app.css`). After a deploy, a browser may run the old script for a while, and an old editor script posts the old JSON shape. Add `?v=<hash>`. Production.

---

## 4. Production (milestone 7, P2) – mostly still open (2026-11-04: 1.4, 1.6, 3.1, 4.2)

- **Background replies on Cloud Run (new).** With request-based billing, Cloud Run throttles the CPU as soon as the response is sent, so a reply running as a `BackgroundTasks` job can stall for minutes or never finish. Move replies to Cloud Tasks (the plan), or turn on "CPU always allocated", before production. A restart between the 200 and the reply still loses the reply (1.1's `handled_at` makes a sweep for those possible).
- **Sync database work in the async webhooks.** `receive_webhook` and `receive_telegram` are `async def` but do the lookup and the commit on the event loop; while they wait for the database, nothing else is served. Run that part with `run_in_threadpool`.
- **No retries on sending:** Telegram's 429 (`retry_after`) and Meta's 429/5xx lose the reply.
- **Postgres:** a naming convention on `Base.metadata`, `JSON().with_variant(JSONB(), "postgresql")`, one squashed baseline migration, the tests run on Postgres too.
- **Logs:** IDs, not names or values, in INFO lines (1.5).
- **The WhatsApp verify token is logged.** uvicorn's access log includes the query string, so `GET /webhook?hub.verify_token=…` writes the token into `logs/wabot.log` (checked in uvicorn's code). Low risk: the token only passes Meta's GET check, not the signed POSTs. But it's a secret in a file, and in production's request logs too. P3.

---

## 5. Scripts and logging (P3)

- **`simulate_month.py` writes to the bot's log file.** It imports `wabot.main`, so it opens `logs/wabot.log` like a running `run.sh`. On Windows two processes can't rotate one file: once it reaches 5 MB, every line from either process fails with "--- Logging error ---" on the console and is lost. Give the simulation its own file, like `run_dashboard.sh` does (`LOG_FILE=logs/simulation.log`), or none.
- ✅ fixed: **`LOG_LEVEL=debug` stops the app at startup (verified).** Lowercase is refused: "Input should be 'DEBUG', 'INFO', 'WARNING' or 'ERROR'". Accept any case with a `field_validator` that uppercases it.
- **Still open (2026-11-04: 6.3):** `trainees.trainer_named()` finds trainers by name, which isn't unique; only scripts and the simulation use it.

---

## 6. Docs out of date (P3)

- **CODE_EXPLANATION, Flow 4:**
  - It says `run.sh` prints "WARNING: set the Callback URL by hand"; it prints "WARNING: the webhook isn't set - see above (tunnel: …)".
  - "Messages sent before that … are lost" holds only on WhatsApp; Telegram keeps them (README says so).
- **CLAUDE.md ("Key decisions") and PLAN.md ("The LLM's role"):** "the trainee is resolved server-side from the sender's phone number". On Telegram it's the chat, linked once by the shared number.
- **"WhatsApp" where it means any messenger:** `weight_chart.js`'s header ("the WhatsApp message it came from"), and CODE_EXPLANATION's "nothing sent to WhatsApp" for the simulation (nothing is sent anywhere).
- **CODE_EXPLANATION, "Data to look at locally":** gives the manual uvicorn command; `bash run_dashboard.sh` does all of it.

---

## 7. Code cleanups (P3) – still open (2026-11-04: 7)

- **Three ways to compare names:** `plans._key`, `exercises._key` (the same function twice) and `name_error()`'s `lower()`.
- **A hidden flush:** `plan_history.assign()` starts with `session.add(trainee); session.flush()`, which flushes every pending change of the caller.
- **Counts without the trainer filter:** `queries.weigh_ins()` and `workouts()` count reports per day without it, unlike `current_per_day()`. Harmless, but inconsistent.

---

## Fixed on 2026-11-13 (the quick ones)

- **1.4:** `main.plan_summary()` notes a plan that doesn't validate in the log line instead of raising; the LLM call then fails inside the `try` and the trainee gets the apology (test in `test_webhook.py`).
- **1.3, the caption half:** a photo's (video's, document's) caption is the message's text on Telegram and WhatsApp, so "yanek" in it gets an answer; the model sees "[wiadomość typu photo] <caption>". Still open: a fixed reply for non-text messages without a caption (voice).
- **3.2:** `?month=` outside 2000–2100 shows this month.
- **5:** `LOG_LEVEL` in any case; `send_test_custom_first_message.py` normalizes `--to`.

## Fixed since 2026-11-04 (checked)

- **2.3 / 3.2:** `users_of()` joins the trainee and filters by the trainer, in one query; the old `current_version()` is gone.
- **6.3:** `send_test.py` uses `Settings`, not its own `.env` parsing.
- **5.2:** the test helpers give plans through `plan_history`.
- **The earlier quick fixes:** 1.1, 1.3, 1.4's timeout, 2.1, 2.2, 2.5, 2.6, 3.1's dev-login guard, three of 3.4, 4.1, 5.1, 6.1, 6.2, 8; decided: 2.3 (a trainee never moves).

## Decisions to make

1. ~~**A "handled" state on inbound messages (1.1).**~~ – built 2026-11-13; the rest of the conversation design (clarifications keep the report waiting, lists, corrections against saved entries) is in PLAN.md for milestone 4.
2. **Several days in one report (1.2):** lists in the schema, or a prompt rule for now.
3. **Non-text messages (1.3):** the caption as text, a fixed reply otherwise. Voice later (transcribing is another OpenAI call per message).
4. **Local Telegram:** polling or the tunnel (2.1).
5. **Phone format (2.2):** +48 by default?
6. **The trigger in a private chat (1.5):** keep "yanek" only, add a reaction, and the spelling ("Janek"?).
7. **Still open:** trainers' own exercises vs the fixed catalog type (2026-11-04: 2.7).

## Things in good shape (keep them)

- **The Telegram port:**
  - Only the sender's own number connects a chat, and a contact card connects nobody.
  - The bot token never reaches a log or a traceback (TelegramError without the URL; httpx and httpcore stay at WARNING at every level, and tests check both).
  - The webhook secret is derived from the token, so there's nothing extra to configure.
  - One history across both channels.
- **Logging:** `LOG_LEVEL`, a rotating file, and one INFO line per message that answers "did it arrive, did the bot answer, with which plan".
- **Tenant guards in one place:** the dependencies (`current_trainer`, `trainee_of_trainer`, `template_of_trainer`, `version_of_trainee`), with tests that another trainer gets 404.
- **Every report kept,** with the newest of the day current; plan versions and day IDs so saved trainings keep their meaning.
- **Checked LLM output:** structured output, validated, retried once with the errors.
- **Migrations tested** against the models, with downgrades and the data migrations.

## Suggested order

1. ~~**1.4** and **1.3's caption**~~ – done (2026-11-13); 1.3's fixed reply for voice messages is still open.
2. **Restart `run.sh` to check the DNS fix.** If Telegram refuses again, build 2.1 (polling).
3. ~~**1.1 (handled state)**~~ – done (2026-11-13); **1.2 (several days)** with milestone 4.
4. **2.2** before the dashboard registers trainees.
5. **Section 4** before milestone 7.
6. **The rest** when you're working on the code it's in.
