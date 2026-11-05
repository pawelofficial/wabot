# Refactor ideas – technical debt review (2026-11-04)

How this was checked: I read every module, template, script, migration and test, then ran the test suite (183 pass), `ruff check` (clean) and `ruff format --check` (clean). I reproduced the suspected bugs with a probe script on a scratch database; `wabot.db` and `simulated_month.db` were only read. Nothing in the code was changed.

**Verified** means I reproduced it. The other findings come from reading the code.

Priorities:
- **P1** – loses or duplicates trainees' data now, or shows one trainer another trainer's data.
- **P2** – fix before the feature that builds on it, or before production.
- **P3** – cleanup.

## Fixed so far (2026-11-04, the quick fixes)

- **1.1** – bot messages before the trainee's first one go to the model as a developer note, so it sees the reminder it's answering.
- **1.3** – letters that are in the trainee's name or messages are allowed in the reply.
- **1.4** – the OpenAI client has a 60 s timeout and one retry (only that part of 1.4).
- **2.1 and 2.2** – `trainees.training_plan` (a copy of the current plan) is replaced by `trainees.plan_version_id`, a pointer to the trainee's own version; each check-in points to the version it was checked against (`training_checkins.plan_version_id`). Both are two-column foreign keys, so the database refuses another trainee's version. Migration 0007, tested on rows in the old shape and on copies of both local databases.
- **2.3** – decided: a trainee never moves to another trainer. `add_trainee.py --trainer` refuses to change an existing trainee's trainer; the library shows only the trainer's current trainees and `current_version()` filters by trainer, as a safeguard.
- **2.5** – a restore or assign that changes nothing adds no version ("Podopieczny ma już ten plan z biblioteki").
- **2.6** – `tests/test_schema.py` fails when an ID disappears (`tests/catalog_ids.txt`).
- **3.1 (dev login only)** – settings refuse `DASHBOARD_DEV_LOGIN` on Cloud Run (`K_SERVICE` set).
- **3.4 (three of them)** – IDs compared instead of identity, `DEFAULT_TIMEZONE` instead of a hard-coded zone, the editor still warns about unsaved changes after a failed save.
- **4.1** – `tests/test_schema.py` runs the migrations on an empty database and compares them with the models, plus downgrade and upgrade again.
- **5.1** – tests don't read `.env`.
- **6.1** – simulated entries are saved at the simulated time.
- **6.2** – `simulate_month.py` deletes the `-wal`/`-shm` files too, and says to stop the dashboard when the file is in use.
- **8** – the out-of-date lines in PLAN.md.

Still open: everything else below, notably 1.2 (the "handled" state), 2.7, 3.1's proxy and session points, 4.2.

## Top of the list

| # | Problem | Priority |
|---|---|---|
| 1.1 | The bot never sees its own message when the trainee answers it (reminders, first messages) – verified | P1 |
| 1.2 | "Already handled" is guessed from the bot's last outgoing message: a failed send duplicates a report, an apology loses one – verified | P1 |
| 2.3 | A trainee moved to another trainer: the old trainer's library still shows them by name; the new trainer sees none of their history – verified | P1 (the leak) / P2 |
| 5.1 | Tests read your `.env`, so their result can depend on your local settings | P2 |
| 2.1 | The current plan is stored twice (`trainees.training_plan` and the newest `trainee_plans` row), and they can disagree – verified | ✅ fixed |
| 2.2 | Check-ins don't record which plan version they were saved against | ✅ fixed |
| 1.3 | A non-Latin letter in a reply (e.g. a Ukrainian name) makes every reply fail – verified | P2 |
| 2.6 | "Exercise IDs are permanent" isn't enforced; renaming one breaks every plan that uses it | P2 |
| 4.1 | No test runs the migrations | P2 |
| 3.1, 1.4, 4.2 | Production gaps: proxy/HTTPS cookie, dev login guard, async webhook doing sync DB work, no LLM timeout, Postgres readiness | P2 (before milestone 7) |

---

## 1. The bot's conversation flow

### 1.1 The bot doesn't see its own message when the trainee answers it (verified, P1) – ✅ fixed

`assistant.to_llm_messages()` (`src/wabot/assistant.py:34`) drops bot turns at the start of the history ("the conversation must start with the trainee"). When the bot's message is the first one in the 24 h window, it is dropped. That's exactly the case of a reminder, or of a message sent with `send_test_custom_first_message.py`.

- **Probe:** the history [bot: "Jak dziś z dietą?", trainee: "yanek dobrze, bez odstępstw"] sends the model only `[{'role': 'user', 'content': 'yanek dobrze, bez odstępstw'}]`.
- **Why it matters:** the "Reply to a reminder" case in PLAN.md depends on the model seeing the question, and milestone 5 (reminders) is built on it.
- **Fix:** this rule is probably left over from Anthropic's API, which required the first message to be a user turn. Check whether OpenAI's Responses API needs it; if not, remove the loop. If it does, turn the leading bot messages into a developer note ("Twoja wcześniejsza wiadomość: …") instead of dropping them. Add a test for a reply to a bot-started message.

### 1.2 "Already handled" is guessed from the bot's last outgoing message (verified, P1)

The marker, and with it "extract only what's new", goes after the bot's last stored outgoing message (`assistant.split_at_last_bot_message`). Two failure modes follow:

- **A reply that fails to send duplicates the report.** No outgoing message is stored, so on the next "yanek" call the report that was already saved is below the marker again.
  - **Probe:** "yanek 80 kg" (the send fails), then "yanek a co jutro?". The second LLM call got both messages as new, and two weight rows were saved.
- **An apology loses the report.** The apology (after the LLM call or the save failed, `main.py:88`) is stored as a bot message. The report before it moves above the marker, and the prompt tells the model "wcześniejsze zgłoszenia zostały już zapisane". The apology says "spróbuj za kilka minut", but "yanek, spróbuj jeszcze raz" won't extract it again. The report is lost unless the trainee types it again.

**Fix:** make the state explicit.
- **Mark handled messages:** give inbound messages a `handled_at` column (or `handled_by_message_id`). Set it when a report from the message was saved, or when a later message covered it (`asked_again`).
- **Move the marker:** put it after the last *handled* message instead of the last outgoing one. Failed sends and apologies then don't move it.
- **Store failed sends too,** with a status (see 1.5), so the history shows what the trainee did and didn't get.
- **Safety net for lost replies** (1.4): find messages that mention yanek and aren't handled after a few minutes.

This is also the base milestone 4 (corrections) needs.

### 1.3 A non-Latin letter in the reply makes every reply fail (verified, P2) – ✅ fixed

`_foreign_letters()` (`src/wabot/llm_output.py:88`) rejects any letter that isn't Latin script.

- **Probe:** a reply of "Dzięki, Олена!" is rejected with "reply contains non-Polish letters 'Оаелн'". After the one retry the trainee gets the apology.
- **Why it matters:** the same happens to a trainee registered as "Олена", or one who sometimes writes in Ukrainian. Trainers in Poland will have such clients.
- **Fix:** allow letters that appear in the trainee's name and in their own messages (pass them to `validate_output()`). Or make it a logged warning instead of a validation error.

### 1.4 Webhook and reply handling for production (P2)

- **Sync database work on the event loop.** `receive_webhook` is `async def` (`src/wabot/main.py:48`), but it does the trainee lookup and the commits synchronously on the event loop. While it waits for the database (up to SQLite's 5 s busy timeout, or a Postgres round trip), nothing else in the process is served. Keep it async for `await request.body()`, and run the storing part with `run_in_threadpool`.
- **Replies run as `BackgroundTasks` in the same process.** A restart between the 200 and the reply loses that reply without a trace. Cloud Tasks is the plan (PLAN.md). Until then, 1.2's `handled_at` makes a sweep possible.
- **No timeout on the OpenAI client** (`assistant.py:108`). The SDK default is 10 minutes with 2 retries, so a hung call keeps the trainee waiting and holds a worker. Set something like `timeout=60, max_retries=1`.
- **No retries on sending:** `whatsapp.send_text` doesn't retry Meta's 429/5xx. P3.

### 1.5 Delivery failures aren't linked to the stored message (P2, with reminders)

`whatsapp.failed_statuses()` is only logged. The bot's message stays in the history as if it was delivered, for example after error 131047 outside the 24 h window. So the model "remembers" telling the trainee something they never got.
- **Fix:** store the delivery status on the message, looked up by its wamid (`messages.status`).
- **Check the window before sending:** nothing on the send path checks `in_service_window()` today (`conversation.py:81`). Reminders must check it and use templates outside the window.

### 1.6 Smaller

- **The trigger is Y-only (verified).** "Janek" (the usual Polish spelling), "Jankowi" and "Yanuś" don't trigger the bot (`conversation.py:19`). Probably on purpose, so a real Janek in the chat doesn't set it off. Decide it, and tell trainees the exact spelling. Product question.
- **First messages use the full report schema.** `generate_first_message()` validates weight and training it then throws away, so a stray training in the output can fail validation for nothing. A schema with only `reply` fits. P3.
- **Logs contain health data.** For example `Saved weight from …: {"kg":82.4,…}` (`main.py:143`) and the invalid-output warnings, which include values. Fine locally. In Cloud Logging it is health data under GDPR, with its own retention. Log IDs, not values, before production. P2 (production).

---

## 2. Data model

### 2.1 The current plan is stored twice (verified) – ✅ fixed (migration 0007)

`trainees.training_plan` (what the bot reads) and the newest `trainee_plans` row are meant to be the same, but nothing enforces it. The code already works around this with `trainee.training_plan is not None` checks in `plan_pages.py` and `plan.html`.

Bugs from it, both verified:
- **An identical edit leaves the trainee without a plan.** `add_trainee.py --no-plan` sets the column to null directly (`scripts/add_trainee.py:107`). If the trainer then saves a plan equal to the last version, `assign()` compares it with that version (`plan_history.py:46`), not with the column. It reports "Plan bez zmian" and the trainee stays without a plan.
  - **Probe:** "version added: None | trainee has a plan: False".
- **A new trainee registered with `--no-plan` gets the example plan in their history:** it's assigned, then removed.

**Decided (2026-11-04): Kasia's row points to her current version.** `trainees.training_plan` (the whole plan copied again) becomes `trainees.plan_version_id`, a foreign key to `trainee_plans.id`. The plan itself is stored once, in `trainee_plans`.
- **The bot** reads the plan of the version the pointer names.
- **No plan:** the pointer is null; the history stays as it is.
- **Only her own versions:** a two-column foreign key, (`plan_version_id`, `id`) → `trainee_plans` (`id`, `trainee_id`), so the database refuses a pointer to another trainee's version.
- **`plan_history.assign()`** adds the version and moves the pointer, in one transaction; removing a plan sets it to null.
- **Migration:** each trainee with a plan points to their newest version; then the JSON column is dropped.
- **Built 2026-11-04** together with 2.2. `plan_history.remove()` replaces setting the column to null.

### 2.2 Check-ins don't record which plan they were saved against – ✅ fixed (migration 0007)

`training_checkins.plan_day` is the day's name as text. `missed_exercises` and `status` were computed from the plan as it was at that moment. Plan versions now exist, so a check-in can point at one.
- **Fix:** add `training_checkins.plan_version_id`, the version that was current when the check-in was saved.
- **What it gives the calendar:** it can show the right day and "4/5, brak: plank" after the plan changes.
- **Day IDs:** ✅ built 2026-11-04 (migration 0008). Every plan day has an ID that stays through renames and copies, and check-ins store it (`plan_day_id`).

### 2.3 Moving a trainee to another trainer (verified, P1 for the leak) – ✅ fixed: decided that a trainee never moves

`add_trainee.py --trainer` (`scripts/add_trainee.py:101`) changes only `trainees.trainer_id`. Their messages, entries and plan versions keep the old trainer's ID. As a result:

- **The new trainer sees nothing of the past.** `history()` and `current_per_day()` filter by the trainee's current trainer, so no weights and no plan history show up.
  - **Probe:** "Ewa sees history versions: 0".
- **The old trainer still sees the trainee's name.** Their library lists the trainee as using one of their plans, with a link that answers 404.
  - **Probe:** Marek's library lists Kasia, who now belongs to Ewa.
  - **Cause:** `users_of()` (`src/wabot/dashboard/plan_pages.py:52`) takes each trainee's newest version among the old trainer's rows and never checks who the trainee belongs to now.
- **An edit by the new trainer links to the old trainer's library.** It copies `template_id` from the old trainer's library plan, because `current_version()` doesn't filter by trainer.

**Fix now:**
- **`users_of()`:** join `Trainee` and filter on the trainee's current trainer.
- **`current_version()`:** filter by trainer like `history()` does.

**Decided (2026-11-04): a trainee never moves to another trainer.** `add_trainee.py --trainer` now refuses for an existing trainee, and the dashboard won't offer it.

### 2.4 Library plan names aren't case-insensitive for Polish letters (verified, P3)

`name_error()` (`plan_pages.py:71`) compares with SQL `lower()`, but SQLite's `lower()` lowercases only A–Z.
- **Probe:** `lower('ŁYDKI Ćwiczenia')` = `'Łydki Ćwiczenia'`.
- **Result:** "Łydki" and "łydki" can both exist on SQLite, and Postgres would refuse the second. Separately, the database's unique constraint is case-sensitive, and two simultaneous saves of the same name give an IntegrityError (500).
- **Fix:** add a `name_key` column (casefolded, spaces collapsed, the same `_key()` as `plans.py`) with the unique constraint on (`trainer_id`, `name_key`). Catch the IntegrityError and show the same Polish message.

### 2.5 Versions that change nothing (verified, P3) – ✅ fixed

Restoring a version equal to the current plan, or assigning a library plan the trainee already has unchanged, adds a version.
- **Probe:** the history went from 3 versions to 4.
- **Fix:** skip it when the plan, template and name are all unchanged, the same rule as an edit. Re-assigning after the library plan changed still adds one.

### 2.6 "Exercise IDs are permanent" isn't enforced (P2) – ✅ test added

Every stored plan, library plan and version is validated against the catalog whenever it's read (`plans.of()`, `TrainingPlan.model_validate(version.plan)`). Renaming or removing one ID in `exercises.yaml` makes every plan that uses it invalid. The bot then apologizes on every message from those trainees, and the plan pages answer 500. A test with a snapshot of the IDs, failing when one disappears, prevents it.

### 2.7 The catalog is a type built at import (P2, decide before Ćwiczenia)

`ExerciseId = Literal[IDS]` (`src/wabot/exercises.py:48`) works for one shared YAML catalog. Trainers' own exercises, stored per trainer in the database, can't be a fixed type:
- **Checks:** validation needs the trainer's set (pydantic validation context).
- **Editor:** the dropdowns need the trainer's list.
- **LLM schema:** its enum must be built per request, or dropped. The plan's IDs are already in the prompt, and `validate_output()` checks them anyway.

`plans.py`, `llm_output.py` and `plan_editor.py` all assume the fixed type, so design this before building Ćwiczenia.

---

## 3. Dashboard

### 3.1 Sign-in in production (P2, production)

- **HTTPS behind Cloud Run's proxy.** The app sees plain http there. `secure=request.url.scheme == "https"` (`src/wabot/dashboard/auth.py:54`) then sets the session cookie without Secure, and `url_for("google_login")` gives Google an `http://` login URI. Run uvicorn with `--forwarded-allow-ips='*'` on Cloud Run, or take both from config.
- **Dev login in production.** `DASHBOARD_DEV_LOGIN=True` there would let anyone sign in as any trainer. Without `DASHBOARD_SESSION_SECRET`, the cookie key is also the public `DEV_SECRET` (`auth.py:31`). Refuse to start with dev login on Cloud Run (`K_SERVICE` is set).
- **Sessions can't be revoked.** They last 30 days. Removing a trainer's email doesn't sign them out, because `current_trainer()` only checks that the trainer exists (`auth.py:80`). Check the email there, or keep a `session_version` in the cookie.
- **No CSRF tokens.** SameSite=Lax already stops other sites' POSTs in current browsers, which is enough for now. As defense in depth, reject POSTs whose `Origin` isn't the dashboard's. P3.

### 3.2 `users_of()`

Besides the leak in 2.3, it calls `session.get(Trainee, …)` once per trainee. Load the trainees in one query.

### 3.3 Structure (P3)

`routes.py` holds the page renderer and the Jinja filters. `plan_pages.py` imports `render()` from it, and `routes.install()` imports `plan_pages` inside a function to get around the circular import (`routes.py:74`).
- **Fix:** move `templates`, the `pl_*` filters and `render()` into `dashboard/pages.py`, which both routers import.
- **Then:** `install()` can live in `dashboard/__init__.py`.

### 3.4 Small things (P3)

- **A second ownership check:** `assign_library_plan` repeats `template_of_trainer`'s check inline (`plan_pages.py:194`), because the ID comes from the form. A helper used by both keeps "who may see what" in one place.
- **Comparing by identity:** `is_current=current is version` (`plan_pages.py:251`) relies on the session's identity map. Compare IDs.
- **Hard-coded timezone:** trainers have none, so the library shows times in `"Europe/Warsaw"` (`library.html:19`). Use `DEFAULT_TIMEZONE`, or add `trainers.timezone` when there's a settings page.
- **A developer term on screen:** `plan_history.SOURCES` shows "skrypt add_trainee" to trainers. Change it when registration moves to the dashboard.
- **Notices that come back:** the `?done=` notice reappears on reload or from a bookmark. A flash cookie fixes it if it bothers anyone.
- **Editor, after a failed save:** the 422 page starts with `dirty=false`, so leaving loses what was typed without asking. Mark the page dirty when errors are shown.
- **Editor, with a bigger catalog:** every exercise row has two full catalog dropdowns. With per-trainer catalogs of 100+ exercises, the page grows and picking gets slow; a searchable picker fixes both.
- **No size limits:** nothing limits the plan's text fields or the posted JSON. Only signed-in trainers can post, so low.

### 3.5 Phone numbers when the dashboard registers trainees (verified, P2 with "Dodaj podopiecznego")

- **Country code:** `normalize_phone("600 123 456")` returns `"600123456"`, accepted without a country code. WhatsApp sends `48600123456`, so that trainee would always get "not registered". Add +48 to 9-digit numbers, or require the + form.
- **Numbers already taken:** `trainees.phone` is unique across all trainers. Registering a number another trainer already has must fail without saying which trainer has it.

---

## 4. Database and migrations

### 4.1 No test runs the migrations (P2) – ✅ test added

`tests/conftest.py:29` creates the tables with `Base.metadata.create_all`, so the migrations are only ever tried by hand. A test could:
- **Check the schema:** run `alembic upgrade head` on an empty database and compare it with the models, which is what `alembic check` does.
- **Check downgrades:** downgrade, then upgrade again.

That catches a migration that drifts from `models.py`.

Right now `uv run alembic check` fails, but only because `wabot.db` is at 0005 ("Target database is not up to date"). Both local databases are at 0005; `run.sh` and `run_dashboard.sh` migrate them on their next start.

### 4.2 Before Postgres (milestone 7, P2)

- **No naming convention.** `Base.metadata` has none, so unnamed constraints get names that differ between databases. Migration 0004 already needed `_unique_name()` to guess them. Add `MetaData(naming_convention=...)`.
- **Squash the migrations.** There's no production data yet, so squash 0001–000N into one baseline migration with the naming convention before the first Postgres deploy. That keeps SQLite's batch-mode workarounds out of production.
- **JSON type.** `sa.JSON` becomes Postgres `json`, which has no equality operator, so no `=`, `DISTINCT` or `GROUP BY` on plans. Use `JSON().with_variant(JSONB(), "postgresql")`.
- **Test on Postgres too.** Run the test suite in a Docker container. `lower()`, JSON and timestamps behave differently there, as 2.4 shows.

---

## 5. Tests and tooling

### 5.1 Tests read your `.env` (P2) – ✅ fixed

`Settings` reads `.env` (`src/wabot/config.py:10`), and conftest sets only the WhatsApp and OpenAI values. Anything else in your `.env` changes what the tests run with: `HISTORY_MODE`, `DAILY_UPDATES_ONLY`, `GOOGLE_CLIENT_ID`, the `DASHBOARD_*` values. A test can pass or fail depending on your local file.
- **Fix:** turn the env file off for tests (e.g. `Settings.model_config["env_file"] = None` in conftest, before importing the app), and set what tests need explicitly.

### 5.2 Test helpers

The bot tests give trainees plans by setting `training_plan=` directly: `test_webhook.py:33`, `test_conversation.py:39`, `test_llm_output.py:34`. That's fine for those tests. If 2.1 changes how the plan is stored, move them to one helper.

### 5.3 What isn't tested yet

- the reply to a bot-started message (1.1)
- the failure paths of 1.2
- migrations (4.1)
- that catalog IDs stay (2.6)

---

## 6. Simulation and scripts

### 6.1 Simulated entries are timestamped "now", not in October (verified, P3) – ✅ fixed (rebuild simulated_month.db to see it)

Messages get the simulated time, but `weight_entries` and `training_checkins` take `created_at` from the model default (`models.py:125`).
- **Data:** in `simulated_month.db` every entry is from 2026-11-04 10:44, while their dates are 2–30 October.
- **Why it matters:** any dashboard view that shows when something was reported, or uses `created_at` (e.g. the overview's "last report"), will look wrong on the simulation's data.
- **Fix:** have `entries.save()` take `now` from its caller (`main.reply` already has it).

### 6.2 `simulate_month.py` deletes the database but not its `-wal` / `-shm` files (P3) – ✅ fixed

`simulate_month.py:65` unlinks only the `.db` file. A WAL left by a server that had the old file open can be replayed into the new file. On Windows the unlink simply fails while the dashboard server runs. Delete all three, and when the file is locked, say "stop the dashboard first".

### 6.3 Other

- **Duplicated settings:** `send_test.py` parses `.env` itself and has its own `GRAPH_API_VERSION`. Use `Settings`. P3
- **Trainers matched by name:** `trainees.trainer_named()` looks trainers up by name, which isn't unique. Only scripts use it; it goes away with the dashboard. P3

---

## 7. Code cleanups (P3)

- **Three ways to compare names:** `plans._key` and `exercises._key` (both casefold and collapse spaces), and `name_error`'s `" ".join(name.split())` + `lower()`. Use one helper.
- **A hidden flush:** `plan_history.assign()` calls `session.add(trainee); session.flush()` first to get IDs, which flushes every pending change of the caller. Document it, or pass the IDs.
- **Filters that disagree:** `plan_history.history()` filters by trainee and trainer, `current_version()` only by trainee (see 2.3).
- **A missing trainer filter:** `queries.weigh_ins()` counts reports per day without the trainer filter `current_per_day()` uses. Harmless, but inconsistent.

---

## 8. Docs that are out of date (P3) – ✅ fixed

- **`docs/PLAN.md` Decisions table:** "Trainee plans | Free text per trainee". They're built from the catalog now.
- **`docs/PLAN.md` Architecture:** "AI | … tool use". It's structured output; the read tools are still open.
- **`docs/PLAN.md` MVP simplifications:** "a small CLI command plus a Firebase Auth user". Sign-in is Google, matched by email, with no Firebase.
- **`docs/PLAN.md` Core data model:** "Built so far (2026-11-03)" doesn't list `plan_templates` and `trainee_plans`.

---

## 9. Design decisions to make

These aren't bugs, but several later features depend on them.

1. **A "handled" state on inbound messages (1.2).** Retries, reminders and corrections build on it. Recommended before milestone 4.
2. ~~**One place for the current plan (2.1)**~~ – done: `trainees.plan_version_id` points to the current version.
3. ~~**Stable day IDs (2.2)**~~ – done: `PlanDay.id`, kept through renames and library copies; check-ins store it (`plan_day_id`).
4. ~~**Two sessions on one day**~~ – decided 2026-11-04: the newest report of each kind (the plan's, outside the plan: `training_checkins.outside_plan`) is current.
5. ~~**Moving trainees between trainers (2.3)**~~ – decided: never.
6. **Trainers' own exercises vs the fixed catalog type (2.7).**
7. **Trainees who aren't Polish (1.3):** the replies stay Polish, but names and messages in other scripts should work.
8. **The bot's name spelling (1.6).**

## Things in good shape (keep them)

- **Tenant guards in one place:** the dependencies (`current_trainer`, `trainee_of_trainer`, `template_of_trainer`, `version_of_trainee`), with tests that another trainer gets 404.
- **Every report kept,** with the newest of the day current. Nothing a trainee sent is overwritten.
- **Checked LLM output:** structured output, validated with plausibility and plan checks, retried once with the errors.
- **Plan library copies:** assigning a library plan copies it, so trainees' plans and their saved trainings never change behind them.
- **Timestamps:** `UTCDateTime` refuses naive datetimes.
- **Simulation:** the scenarios replay the real reply flow, and a test checks that every column is filled.

## Suggested order

1. **1.1 and 1.2, with tests.** They lose or duplicate trainees' data today.
2. **The `users_of()` / `current_version()` filters (2.3) and `.env` in tests (5.1).** Both are small.
3. ~~**2.1 and 2.2**~~ – done (2026-11-04).
4. **The migration test (4.1) and the catalog ID snapshot (2.6).**
5. **3.1, 1.4 and 4.2 before production.**
6. **The rest** when you're working on the code it's in.
