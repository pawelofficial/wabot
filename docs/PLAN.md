# wabot – project plan

A messenger assistant for personal trainers (on Telegram for now, WhatsApp later; see "Messenger" below). Trainees report weight, training and diet adherence in free-form Polish messages; an LLM turns them into structured records, holds short back-and-forth conversations, and answers questions about the trainee's arrangement with their trainer. Trainers manage everything in a web dashboard.

## Decisions

| Topic | Decision |
|---|---|
| Tenancy | Many trainers; each sees only their own trainees |
| Language | Polish (bot replies, reminder templates, dashboard) |
| Trainee plans | Training plans built from the exercise catalog (days, exercises, allowed replacements, notes), edited in the dashboard and kept as versions; diet plan and notes free text (later) |
| Reminders | Yes – the bot messages trainees on a schedule |
| Missed reports | Trainer sees them on the dashboard; no alerts |
| Trainer interface | Dashboard only (trainers don't chat with the bot) |
| Messenger | Telegram for now, WhatsApp kept in the code; one setting switches (`CHANNEL`, decided 2026-11-12, see below) |
| Bot number | One shared bot for all trainers; trainees are mapped to their trainer by phone number (on Telegram, the number they share with the bot once) |
| Consent (GDPR) | Collected on paper, offline; the app only records that it's on file |
| Photos | Stored and shown on the dashboard; not sent to the LLM (after the MVP) |
| Trainer accounts | Created by hand by the operator |
| Changing trainers | Never: a trainee stays with the trainer who registered them, with all their messages, entries and plans (decided 2026-11-04) |

### Messenger: Telegram now, WhatsApp later (decided 2026-11-12)

Meta restricted the WhatsApp Business Account used for development ("activity that does not comply with WhatsApp Business terms"; the review asks for business verification, which needs a registered business). A Telegram bot needs none of that: a token from @BotFather, no review, no 24-hour window, no paid templates. So the bot runs on Telegram until trainers say their clients would rather use WhatsApp; then it moves back.

- **One setting switches**: `CHANNEL=telegram` (default) or `CHANNEL=whatsapp` in `.env` picks the webhook `run.sh` registers, where messages the bot starts itself go (first messages, later reminders), and which settings are required. Replies always go back on the channel the trainee's message came from, so both webhooks can be live at once.
- **Everything else is shared**: trainees, plans, entries and the dashboard don't know the channel. `messages` is one table with a `channel` column, so a trainee's history continues across a switch.
- **Who is writing**: WhatsApp sends the phone number with every message; Telegram doesn't. A new Telegram chat gets a "Udostępnij numer telefonu" button; the trainee presses it once, the bot matches the number to the one the trainer registered and keeps the chat ID (`trainees.telegram_chat_id`). Only the sender's own number counts (Telegram has verified it), not a contact card. Trainers still register trainees by phone.
- **What moving back to WhatsApp needs**: business verification (a registered business, e.g. a sole proprietorship), the restriction lifted or a new account, approved templates for reminders.
- **Reach**: far fewer people in Poland have Telegram than WhatsApp. Ask the first trainers whether their clients would install it.

## Trainee onboarding

Because consent is collected on paper, **the trainer registers each trainee in the dashboard** (name, phone number, consent-on-file date). There are no self-service join codes. On Telegram the trainee then opens the bot (`t.me/<bot>`) and shares their number with one button; that connects the chat to the registered trainee. A message from an unregistered number gets a fixed Polish reply ("Ten numer nie jest zarejestrowany – skontaktuj się ze swoim trenerem") to every message, and nothing is stored beyond what deduplication needs: the message ID, number and type, not the text. Until the dashboard exists, `scripts/add_trainee.py` registers trainees and their trainer (decided 2026-11-03).

## What the bot handles

| Case | Example | Behavior |
|---|---|---|
| Submission | "82,4 rano, nogi zrobione, wieczorem pizza 😬" | Save weight, training and diet entries; confirm what was saved |
| Clarification | "82" → "kg?" → "tak" | Short back-and-forth until the entry is complete |
| Question about the arrangement | "co mam w czwartek?" | Answer only from the trainer's plan text; say so if the plan doesn't cover it |
| Correction | "sorry, 81,4 nie 18,4" | Correct the trainee's most recent matching entry |
| Off-topic / advice | "mogę brać kreatynę?" | Redirect to the trainer; no medical or nutrition advice of its own |
| Reply to a reminder | Reminder: "Jak dziś z dietą?" → "dobrze, bez odstępstw" | Interpreted in context of the reminder (it's part of the chat history) |
| Voice message | "waga 82, nogi zrobione" said into Telegram | Built 2026-11-13: transcribed by OpenAI (`gpt-4o-mini-transcribe`, Polish) before it's stored; the transcript is the message's text, so it's handled like a typed message, without "yanek". Up to 5 minutes; otherwise, or if it fails, a fixed reply. The audio isn't kept; it goes to OpenAI for the transcription (to mention in the consent). Telegram only for now |
| Photo | progress or meal photo | After the MVP: stored in Cloud Storage and shown on the dashboard. In the MVP: a fixed reply that photos aren't supported yet (not built); any caption is processed like a normal message (built 2026-11-13: the caption is the message's text, so "yanek 80,4" under a photo of the scale works) |

## The LLM's role

The bot is called by name: only messages that mention **"yanek"** (any Polish case – "yanku", "yanka") get an LLM call and a reply. Other messages are stored and become part of the history, so a report can be spread over several messages and end with "yanek, zanotuj". Each trainee chats one-to-one with the bot's number (the trainer isn't in the chat) and is told to address reports to Yanek.

**Group chats: maybe later (decided 2026-11-03).** The product is a one-to-one chat between trainee and bot; trainers log or correct entries on the dashboard. Groups wait until there is an Official Business Account (see below). The idea: one WhatsApp group per trainee, with the trainee, their trainer and the bot. Trainer and trainee talk normally, and either of them reports to the bot by name ("yanek dzisiaj waga 80,0"; the trainer: "yanek, zanotuj tę wagę" about the trainee's earlier message). That's why the name trigger exists and why messages without it are kept as history. What this needs (not built):

- Meta's [Groups API](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/): only for an Official Business Account (the blue badge: Meta Business Verification of a registered business – a JDG is enough, it needn't be a sp. z o.o. – plus 30 days on the platform, 2FA, an approved display name, and Meta judging the brand notable, which a new brand may fail), max 8 participants per group, members join through an invite link the business creates. The free test number can't use it.
- Messages keyed by group, not only by phone, with the sender stored on each message.
- The trainee resolved from the group (one trainee per group), never from the sender, since the trainer also reports.
- History given to the LLM with who said what (e.g. "[trener]" / "[podopieczny]"), and a prompt for a three-person chat instead of a one-to-one one.
- Each extracted entry records who reported it (trainee or trainer).

One LLM call per such message, with:

- **Instructions** – the static system prompt from `src/wabot/prompts.yaml`, followed by the **trainee's name** (so the Polish forms match: zrobiłaś / zrobiłeś) and **training plan injected** into it: its days and exercises with their catalog IDs, names, aliases and the replacements the trainer allows (not the whole catalog). Static part first, so the shared prefix stays cacheable.
- **Recent history** – the trainee's last messages within a short window (e.g. 24 h, capped at N messages), including the bot's replies and reminders, so clarifications and replies to reminders work. A "--- NOWE WIADOMOŚCI ---" marker before the first message still waiting (not yet handled by a reply, `messages.handled_at`; since 2026-11-13, before that it went after the bot's last message) shows where the new report starts; data is extracted only below it. `HISTORY_MODE=new_only` makes the code drop everything above the marker instead, in case the LLM re-extracts old reports (decided 2026-11-03; less context, so corrections and answers to the bot's questions lose what they refer to).
- **The trainee's local date** after the history, so "wczoraj" / "w poniedziałek" resolve correctly.
- **Structured output** (decided 2026-11-03, replacing the write tools for now) – a JSON schema with `reply` (the Polish WhatsApp text), `weight` (`kg`, `date`) and `training` (`date`, `plan_day` as named in the plan, `status` done/partial/skipped, `exercises` with a catalog `exercise_id`, sets/reps/weight, `replaces` (the ID of the plan exercise a substitute was done instead of) and a free-text `comment`, `notes`), each null when not reported. `missed_exercises` (plan exercise IDs) is not asked of the model: see the next point. The app saves what it gets and only then sends the reply (a failed save sends the apology instead); only data from the messages still waiting (not yet handled by a reply) is extracted.
- **What isn't reported counts as not done** (decided 2026-11-03). The model only says which exercises were done or replaced and which plan day it was. The code works out the rest. Missed = the plan day's exercises that aren't in the report as done or replaced. Status = `done` when nothing is missed, otherwise `partial` (`skipped` comes from the model). So "zrobiłem tylko klatę" and "skos 4x10" both save the chest exercise as done and the rest as missed. The bot doesn't ask about the rest. It says what it saved and what's missing, and that a full report for the day replaces it. Muscle-group words ("klata", "plecy") mean that day's exercises for the group, so they aren't aliases of single exercises in the catalog.
- **Every report is kept; the newest one per day is the current value** (decided 2026-11-03, replacing "one per day, a newer one overwrites it"; for trainings the newest of each kind, the plan's and one outside the plan, decided 2026-11-04, see "Two kinds of training a day" under the dashboard's step 4). "yanek 80 kg", then "yanek sorki, 81" leaves two weight rows for the day, 80 and 81, and readers (dashboard, reports) take the newest row of each day (highest `id`). Nothing a trainee sent is lost, and the data can be cleaned up or combined in the database later. `DAILY_UPDATES_ONLY=True` switches back to one row per day that a newer report overwrites. For a correction or addition to a training ("przysiad był na 65 kg", "dorzuć plank") the LLM returns the whole session again, copying the rest from the earlier report in the history. That only works while the earlier report is in the history it gets (24 h / 20 messages, and not with `HISTORY_MODE=new_only`); otherwise the newest row of the day has just the corrected part. Showing the LLM the saved entries of the last days fixes that (milestone 4).
- **Messages sent close together** (decided 2026-11-03): when the trainee calls the bot again before the first reply is out ("yanek 80", "yanek sorki, 81"), only the later message is answered. Its turn has both as new, since both are still waiting. An older report also never overwrites an entry saved from a newer message.
- **Exercise catalog** (decided 2026-11-03) – `src/wabot/exercises.yaml` gives every exercise a fixed ID (e.g. `leg_press` for "suwnica", "wypychanie nóg", "leg press"), so progress can be charted per exercise however it's named and across plan changes. The schema only allows catalog IDs. Starts with 22 exercises maintained in the repo; trainers adding their own is a dashboard feature (milestone 3).
- **Training plans are built from the catalog** (decided 2026-11-03, replacing free-text plans). A plan is days of catalog exercise IDs, each with optional sets, reps, weight, a note, and the **replacements the trainer allows** (also IDs), plus free-text notes for everything else (goal, cardio days, remarks). They're stored as JSON on the trainee, edited in the dashboard (since 2026-11-04) or written as YAML files (`plans/example.yaml`) and registered with `scripts/add_trainee.py`; both reject unknown IDs. Why: sending the whole catalog with every message is mostly noise, but without it the model can't map "siady" or "suwnica" to an ID. The plan's exercises and replacements, with their aliases, are what a report almost always mentions, so only they go into the prompt. The model sets IDs only for those; any other exercise ("pompki zamiast wykroków" when push-ups aren't listed) gets its full Polish name, and the code matches that name against the catalog's names and aliases (null if nothing matches). A replacement the trainer didn't list is still saved, with `replaces`; the trainee can ask the bot what they may do instead of an exercise.
- Read tools (`get_diet_plan`, `get_trainer_notes`, `get_recent_entries`) are still open; the training plan is injected rather than fetched by a tool.

The trainee identity is resolved server-side from the sender's phone number and bound to the request (plan lookup, saving); the model never supplies a trainee ID, so one trainee can't reach another's data. The LLM writes the reply text (in Polish).

### Conversation handling (designed 2026-11-13)

How a "yanek" message turns into a reply, case by case (a simple report, a report over several messages, corrections, the bot's clarifying questions and the answers to them, several days at once, plan questions, reminders, diet, photos and voice, failures), with the fallback for each step: [CONVERSATION.md](CONVERSATION.md). Built so far: the "handled" state (`messages.handled_at`): a reply reads the trainee's messages that are still waiting and marks them handled in the same transaction as what it saves, so a failed send doesn't save a report twice and an apology or the help after `/start` doesn't lose one. The rest, a `ConversationHandler` class with the planned cases, is a draft for review.

## Reminders

- The trainer sets a reminder schedule per trainee in the dashboard (e.g. weigh-in Mon/Thu 8:00, diet check daily 21:00, training check after planned session days).
- Cloud Scheduler calls the service every ~15 min; it finds reminders that are due (in each trainee's timezone) and sends them.
- Telegram has no restrictions here: a bot may write to anyone who has started a chat with it, at any time, for free.
- WhatsApp only allows free-form business messages within 24 h of the trainee's last message. Outside that window, reminders must use **Meta-approved message templates** (category: utility, language: `pl`). Templates are paid per delivered message – check current Meta pricing for Poland.
- Sent reminders are stored in the trainee's chat history so the LLM understands the reply.
- Dashboard "missed report" = no matching submission within some time after a reminder or schedule slot.

## Dashboard (trainer only)

- Sign-in: Google accounts (Sign in with Google; see "Site structure" below).
- Trainee list with last report per category and overdue highlighting.
- Trainee page: weight chart, training calendar (below), training and diet check-in history, photos.
- Edit: training plan (built from the exercise catalog: days, exercises, sets/reps/weight, allowed replacements), diet plan, notes (free text), reminder schedule.
- Register trainee: name, phone, consent-on-file date.
- Exercises: add the trainer's own; map reported exercises that have no ID (see "Open ideas").
- Every query filtered by the signed-in trainer's ID.

### Site structure (decided 2026-11-04; step 1 built)

Same FastAPI app as the bot, server-rendered Jinja templates plus HTMX, charts drawn as SVG by small scripts of our own (Chart.js only if a chart needs it). No JS build step: htmx is vendored into `static/` when first needed, styles are one plain CSS file.

**Layout.** Every page extends `base.html`: a left sidebar that folds to icons (the state is kept in `localStorage`); on a phone it becomes a drawer behind a ☰ button, since trainers will often check from their phone. Top to bottom:

1. Trainee dropdown – this trainer's trainees only. Choosing one opens the same section for that trainee (on "Waga" for Kasia → "Waga" for Ola).
2. Trainee sections (greyed out until a trainee is chosen): **Waga** (weight chart), **Treningi** (training calendar and check-in history), **Plan treningowy** (the plan, with an Edit button); Treningi and Plan treningowy are separate views (decided 2026-11-04). Later: Dieta, Wiadomości (the WhatsApp chat as the bot saw it), Przypomnienia.
3. Trainer sections, separated by a line: **Podopieczni** (the home view), **Biblioteka planów** (plans not tied to a trainee, added 2026-11-04), later Dodaj podopiecznego, Ćwiczenia.
4. At the bottom: the trainer's name and Wyloguj.

**Home view = trainee overview.** One row per trainee: last weight and its date, last training and its status, sessions this week, and highlighting when a report type is older than a few days (the MVP "missed reports" rule). Clicking a row opens that trainee's Treningi. It needs no trainee chosen, which is why it's the home view.

**URLs carry the trainee**, not the session, so pages can be bookmarked and the back button works. UI text is Polish, code and URLs English:

| URL | Page |
|---|---|
| `/dashboard` | Podopieczni (overview) |
| `/dashboard/trainees/{id}/weight` | Waga |
| `/dashboard/trainees/{id}/trainings` | Treningi (`?month=2026-10`) |
| `/dashboard/trainees/{id}/plan`, `/plan/edit`, `/plan/versions/{v}` | Plan treningowy, its editor, an earlier version |
| `/dashboard/plans`, `/plans/new`, `/plans/{t}`, `/plans/{t}/edit` | Biblioteka planów, a library plan, its editor |
| `/dashboard/login`, `/dashboard/logout` | sign-in |

Chart data is embedded in the page as JSON (`<script type="application/json">`), not fetched separately. HTMX is for partial updates where a page needs them; the training calendar didn't (2026-11-04): a day links to its details further down the page and the month is a link (`?month=`).

**Who may see what – one place in the code.** Two FastAPI dependencies that every dashboard route uses: `current_trainer` (from the session cookie; redirects to the login page when there is none) and `trainee_of_trainer` (loads `{id}` and answers 404 when it belongs to another trainer). Queries take the trainer from these, never from the URL or a form. Tests check that Trener Marek gets 404 on Ewa's trainee.

**Sign-in** (built 2026-11-04). "Sign in with Google" (Google Identity Services) with an OAuth client ID from any Google Cloud project, no Identity Platform or Firebase (decided 2026-11-04: Google accounts are the only sign-in method, and this needs no extra service; Identity Platform can come later if trainers need email and password). The login page runs Google's sign-in in the browser, posts the ID token to the server, the server verifies it (`google-auth`) and looks up a trainer by the verified email, then sets its own signed session cookie. Trainers are still created by hand: the operator adds a `trainers` row with the trainer's Google email (new column `trainers.email`, unique), and anyone else gets "Brak dostępu". Chosen over IAP (Identity-Aware Proxy), which needs no login code but puts the whole Cloud Run service behind Google sign-in, so Meta's webhook would need a second service. **Locally** there's no Google: `DASHBOARD_DEV_LOGIN=True` makes the login page a list of trainers to sign in as (never in production). The dashboard is developed on the simulation's data: `simulated_month.db` (three trainees, two trainers, a month of reports), built by the same migrations as the real database, with simulated people that have every field real ones have, emails included (decided 2026-11-04: a field the dashboard needs goes into the scenarios too; a test checks no column is left empty).

**Code layout.**

- `src/wabot/dashboard/routes.py` – an `APIRouter` included by `main.py`; one function per page
- `src/wabot/dashboard/auth.py` – session cookie, `current_trainer`, `trainee_of_trainer`, the dev login, later token verification
- `src/wabot/dashboard/queries.py` – reads for the pages, e.g. the current value per day (the newest row, see "Every report is kept"), so the pages don't each repeat that rule
- `src/wabot/dashboard/templates/` – `base.html` (layout, sidebar), one template per page, `partials/` for HTMX fragments
- `src/wabot/dashboard/plan_editor.py` – the plan editor's form data and checks (Polish errors)
- `src/wabot/dashboard/plan_pages.py` – the trainee's plan pages and the plan library; `src/wabot/plan_history.py` – plan versions and the library
- `src/wabot/dashboard/static/` – `app.css`, `app.js` (sidebar, dropdown), `weight_chart.js`, `plan_editor.js`; later a vendored `htmx.min.js`

New dependencies: `jinja2`, `itsdangerous` (signed session cookie), `python-multipart` (forms), `google-auth[requests]` (verifying Google's ID tokens).

**Order of work.**

1. ✅ (2026-11-04) Skeleton: `trainers.email` migration, the router, `base.html` with the sidebar and dropdown, dev login, the guard dependencies and their tests, an empty page per section. Data: `uv run python scripts/simulate_month.py [--canned]`.
2. Home view (overview table).
3. ✅ (2026-11-04) Waga: the chart (newest value per day, with the original message on hover), tiles for the last weight and the change, a table of every day. Drawn as SVG by a small script (`static/weight_chart.js`) rather than Chart.js: one line and a few dozen points don't need a library.
4. 🚧 Treningi: the calendar (see "Training calendar" below) and the day details.
   - ✅ (2026-11-04) The month calendar (`?month=2026-10`, this month by default, whole weeks Monday first) and below it the month's trainings, newest first. A cell shows the plan's training as its day's letter over ✓ / "4/5" / ✕, coloured by status, and a "+" for training outside the plan; skipped has a dashed border, nothing reported is blank. A count of the plan's trainings per week at the end of each row (the "/3" comes with `sessions_per_week`). Clicking a day jumps to its details (no HTMX needed): the exercises with sets, reps and weight, replacements ("zamiast: ..."), the missed exercises by name, notes, the original message, "poprawiony" when the day's training was reported more than once, and the day's name in the current plan when it was renamed. The letter is A, B, C by the day's position in the current plan (found by the day ID, so a renamed day keeps it, and it matches the legend), or in the version it was saved against for a day no longer in the plan. Tiles: the last training ("9 dni temu"), the month's plan trainings (full / partial), skipped, outside the plan.
   - **Two kinds of training a day** (decided 2026-11-04): a day's current trainings are the newest report of each kind: the plan's (a plan day, a skipped one, or plan exercises whose day isn't known yet) and one outside the plan (a run, a swim; `training_checkins.outside_plan`, `entries.outside_plan()`). So "A in the morning, a run in the evening" is two trainings, while "sorki, to był B, nie A" and the answer to the bot's "który to był dzień?" are corrections and replace the earlier report. Chosen over "a different plan day is another session" because the bot repeats the day it understood precisely so the trainee can correct it, and over a flag from the LLM because that needs the saved entries in the prompt (milestone 4). Not covered: two plan days on one date (the newer replaces the older) and a report moved from outside the plan to a plan day. The prompt still says one training a day; a model that merges a run into the day's plan training (as an extra exercise) is handled too.
   - ✅ (2026-11-05) **Day labels**: each plan day has a label the trainer sets in the editor (`PlanDay.label`, up to 3 characters: "A", "B2", "Pn"; no two days with the same one, any case), shown in the calendar's cells, the legend, the details and before the day's name on the plan's cards. The editor gives every day one: a plan without them starts with A, B, C by position, a new day gets the next free letter, and a label moves with its day. A day without a label (an older plan, a YAML file without `label`, a field left empty) is still labelled by its position. The label isn't in the bot's prompt (the bot names days by their names).
   - Still to do: the plan fields `weekdays` ("A planned, no report") and `sessions_per_week` ("2/3 this week").
5. ✅ (2026-11-04) Plan treningowy: the plan as cards per day (catalog names, sets/reps/weight, replacements as chips) and an "Edytuj plan" button that opens the editor on its own page (`/plan/edit`; decided 2026-11-04: a separate page rather than editing in place, so the plan stays readable and "Anuluj" just leaves). The editor adds, removes and reorders days and exercises, picks exercises from the catalog by muscle group, and adds replacements as chips; on save the plan is checked with the same rules as a YAML plan and errors are listed in Polish, nothing saved. A plain form posting the plan as JSON, no HTMX. The bot uses the new plan from the next message. Still open: the calendar's plan fields `weekdays` and `sessions_per_week` come with step 4 (`label` is built), the trainer's own exercises with Ćwiczenia; saved check-ins keep their plan day name when a day is renamed (since 2026-11-04 a day also has an ID that stays through renames, see below).
   - ✅ (2026-11-04) **Plan history and the plan library.** Every change of a trainee's plan (an edit, a library plan, a restored version) is kept as a version (`trainee_plans`); the plan page lists them, shows any of them and restores one as a new version, nothing deleted. The trainer's **Biblioteka planów** holds plans not tied to a trainee (`plan_templates`), built with the same editor; the trainee's plan page assigns one, or copies the trainee's plan into the library. Decided 2026-11-04: **assigning copies** the library plan to the trainee rather than linking it, so editing a library plan never changes what trainees already have (their plan is what the bot used and what their trainings were saved against); to update a trainee, assign the plan again. The trainee's row points to the current version (`trainees.plan_version_id`; decided 2026-11-04 instead of a copy of the plan on the trainee, so the plan is stored once), and each training check-in points to the version it was checked against (`training_checkins.plan_version_id`), so a later plan change doesn't change what a saved training means; the calendar reads the plan day from that version. **Day IDs** (2026-11-04): every plan day has an ID the trainer never sees, kept when the day is renamed or moved and in copies of a library plan, and each check-in stores it (`plan_day_id`), so the trainings of one day are found across renames and plan versions. A day without an ID (a YAML plan, a day added in the editor) takes the ID of the earlier day with the same name. The LLM still names the day; the code finds its ID.
6. ✅ (2026-11-04, built ahead) Google sign-in. Still to do: create the OAuth client ID and try it against real Google (README, "Dashboard").

### Training calendar (idea, noted 2026-11-03)

A month view per trainee (built 2026-11-04, see step 4 above). Each day shows the newest training check-in of that day, labelled with the plan day's short label ("A"). Clicking a day shows the exercises, weights, replacements, notes and the original WhatsApp message.

- **Colour is the saved `status`**, which the code computes from the plan day and the report (see "What isn't reported counts as not done"). Green: `done`, every exercise of that plan day was done or replaced. Amber: `partial`, with `missed_exercises` shown as e.g. "4/5, brak: plank". Red, with a dashed border: `skipped` (the trainee said so). Its own colour and an icon: training outside the plan (`plan_day` null, e.g. running). Blank: nothing reported. Sets or weight below the plan don't count at first; "done but lighter" can become its own shade if trainers want it.
- **"Skipped" and "no report" look different.** Skipped is something the trainee said; no report is unknown. Once reminders exist, "no report after a reminder" is the state trainers care about.
- **Two kinds of plan, two meanings of "missed":**
  - Weekday plans (A on Monday, B on Wednesday): every date has an expected session, so a day with no report shows "A planned, no report" (outlined, not filled).
  - Rotation plans (A, B, C in turn, N times a week): no date has an expected session, so the calendar shows only what was done, plus a weekly counter ("2/3 this week"), and maybe a note when the order was broken (B never came between A and C).
- **Plan fields this needs**: a day ID that stays through renames (built 2026-11-04, `PlanDay.id` / `training_checkins.plan_day_id`); `label` on each day (built 2026-11-05: the letter for the cell; it can't reliably be parsed from the name); not built yet: optional `weekdays` on each day (a plan with them is a weekday plan), and `sessions_per_week` on the plan (for rotations). The dashboard's plan builder offers the two kinds.
- **Two sessions on one day: decided 2026-11-04**, the newest report of each kind (the plan's, outside the plan) is current; see step 4 above.

## Architecture

| Layer | Choice |
|---|---|
| Messenger | Telegram Bot API (`CHANNEL=telegram`, webhook `/telegram/webhook` with a secret header) or Meta WhatsApp Cloud API (`CHANNEL=whatsapp`, webhook `/webhook` with a signature), both called directly with httpx |
| Bot + dashboard | Python, FastAPI on Cloud Run (`europe-central2`) |
| Async processing | Webhook verifies the signature or secret, stores the message (unique `external_id` = dedup), acks 200, enqueues Cloud Task |
| AI | OpenAI Python SDK (Responses API), structured output (read tools still open); default model `gpt-5.6-luna` (switched from Anthropic on 2026-11-01 – existing OpenAI credit; from `gpt-5.4-mini` to Luna on 2026-11-03 – about 4x cheaper and as good in tests) |
| Database | Cloud SQL Postgres, SQLAlchemy + Alembic |
| Files | Cloud Storage (photos) |
| Scheduling | Cloud Scheduler → reminders endpoint |
| Secrets | Secret Manager |
| Dashboard UI | Server-rendered Jinja + HTMX, charts as SVG drawn by our own small scripts |
| Tooling | uv, ruff, pytest; cloudflared/ngrok tunnel for local webhook testing |

### Core data model

`trainers` → `plan_templates` (the trainer's plan library) and `trainees` (phone, name, timezone, consent_date, plan_version_id → the current version; diet_plan and notes later) → `trainee_plans` (every version of the plan, stored once), → `weight_entries` (date, kg), `training_checkins` (date, plan_day, status, missed_exercises, notes) with `training_exercises`, later `diet_checkins`, `photos`, `reminder_schedules`; and `messages` (inbound and outbound on either channel, used as chat history and for dedup; `handled_at` on a trainee's message once a reply has dealt with it). Every trainee-owned row also carries `trainer_id`; exercise rows are reached only through their check-in, which carries it. Each entry keeps `message_id`, the trainee's message the bot answered when saving it, so the dashboard can show the original next to the entry. Built so far (2026-11-04): trainers, trainees, messages, weight_entries, training_checkins, training_exercises, plan_templates, trainee_plans.

## Out of scope for now

- Trainers chatting with the bot (an idea for later, see "Open ideas"), and alerts or notifications to trainers.
- Self-service trainee sign-up, and consent collection in the app.
- Billing or subscriptions for trainers.
- The bot giving its own training, diet or medical advice.

## MVP simplifications

The goal is the smallest version that a trainer can use with real trainees. Where a question came up, the MVP takes the simplest answer:

- **Trainer accounts** are created by hand by the operator (`scripts/add_trainee.py --trainer-email`; the trainer signs in with that Google account). No sign-up page.
- **Chat history** sent to the LLM is the trainee's last 20 messages from the past 24 h.
- **Corrections:** a newer report for the day is the current value; the older one is kept as a row (see "The LLM's role").
- **Missed reports:** the dashboard shows the date of each trainee's last weight, training and diet report, and highlights any older than a few days. There are no rules tied to the reminder schedule.
- **Reminders:** a simple per-trainee schedule (days of the week plus a time) for each report type, with one Meta template per type.
- **Not in the MVP:** photos, notifying trainees about plan changes, and data-retention and export tooling (data is deleted by hand on request).

## Risks

- **Meta business verification and template approval** are slow and outside our control, and Meta restricted the development account in November 2026 (see "Messenger"). On Telegram neither applies; they matter again only when moving to WhatsApp.
- **Health data under GDPR.** Keep all GCP resources in EU regions, and note that the OpenAI API processes message text (requests are sent with `store=false`) (check its data-processing terms and retention). The paper consent should mention both.
- **Parsing mistakes.** Every save is confirmed back to the trainee, and corrections are supported. The dashboard shows the original message next to each entry so the trainer can check it.
- **Cost:** LLM tokens per message and Meta template fees per reminder. Estimate both once message volumes are known.

## Milestones

### MVP

0. **Developer setup** ✅ done (2026-10-29)
   - Meta developer account (free, uses a personal Facebook login) and a Meta app with the WhatsApp product. Creating the app asks for a business portfolio – just a name, no documents. Meta's prompts to verify the business can be ignored for development.
   - The app's free test number sends to your personal number (a test recipient; up to 5), so you can chat with the bot as a trainee. `scripts/send_test.py` sends a template message using the settings in `.env`; the temporary access token expires after about 24 h, and also stops working early when it's regenerated or app settings change. A System User token with expiration *Never* avoids the daily renewal (see README).
   - Webhooks need two things: the `messages` field subscribed in the app's webhook configuration, **and** the app subscribed to the WhatsApp Business Account (`POST /<WABA_ID>/subscribed_apps` with the access token). The dashboard's Test button works without the second, real messages don't. Repeat this for the production account in milestone 6.

Milestones 1–5 are developed and run **locally** (`bash run.sh` migrates the database, sends a test message and starts the bot; see README): FastAPI on the laptop, a cloudflared/ngrok tunnel for Meta's webhook, and a SQLite file as the database. GCP is set up in milestone 7, where SQLite is replaced by Cloud SQL Postgres. To keep that switch easy, all database access goes through SQLAlchemy and Alembic, avoids SQLite- and Postgres-specific features, and stores timestamps in UTC.

1. **Echo bot** ✅ done (2026-10-29) – webhook verification, signature check, dedup, reply; tested locally via tunnel.
2. **Submissions** ✅ done (2026-11-04) – trainee lookup by phone, database, saving the extracted entries, confirmations.
   - Done (2026-10-29): the LLM (`src/wabot/assistant.py`; OpenAI since 2026-11-01) replies in Polish instead of the echo, using the trainee's last 20 messages from the past 24 h and today's date in Warsaw. Nothing is saved yet besides the messages themselves, so the bot says it "noted" a report. Model is set by `OPENAI_MODEL` (default `gpt-5.6-luna`, low reasoning effort).
   - Done (2026-11-03): the bot answers only when called by name ("yanek"), returns weight and training as JSON next to the reply, and gets the training plan injected into its prompt.
   - Done (2026-11-03): `trainers` and `trainees` tables, the trainee looked up by phone (fixed reply for unknown numbers), each trainee's own plan and timezone from the db, `weight_entries` and `training_checkins` / `training_exercises` filled from the extracted JSON (every report a new row, the newest per day is current; `DAILY_UPDATES_ONLY` overwrites instead), messages linked to their trainee, and `scripts/add_trainee.py` to register a test trainer and trainee.
   - Done (2026-11-03): training plans built from the exercise catalog, with replacements chosen by the trainer; only the plan's exercises go into the prompt (see "The LLM's role").
   - Moved to milestone 4: diet reports, which the bot only acknowledges.
3. **Dashboard** 🚧 in progress – trainer login, trainee registration, plan editing, weight chart, check-in history, last-report view (see "Site structure"). Moved ahead of the conversation milestone on 2026-11-04: basic reporting works, and seeing the saved data makes the conversation problems easier to spot.
4. **Conversation** – clarifications, plan questions, corrections, diet reports; designed case by case in [CONVERSATION.md](CONVERSATION.md). The chat history (24 h / 20 messages) and the handled state already exist. Corrections need the LLM to see the trainee's saved entries of the last days (injected, or a `get_recent_entries` tool), so a correction of an older training doesn't replace the day with just the corrected part. Also open from the real-model simulation (2026-11-04): whether a moved workout on a rotation plan counts as skipped, and whether "trening 2 zrobiony!" plus one exercise means the whole day was done.
5. **Reminders** – per-trainee schedules, Meta templates, Cloud Scheduler.
6. **Go-live accounts** – on Telegram: a production bot from @BotFather (name, picture, description). The WhatsApp steps below only when moving to WhatsApp; Meta's steps can take days to weeks.
   - Meta business verification (company documents); unverified accounts have low messaging limits.
   - Production bot number: a Polish prepaid SIM (decided – not a personal number, which would lose its WhatsApp account), never registered on WhatsApp. It only has to receive one verification SMS or call – no device stays connected – but keep the number active so it can be re-verified later.
   - Bot display name (Meta reviews it).
   - Submit the Polish reminder templates for approval.
7. **Production** – GCP project in `europe-central2`, Cloud Run deploy, Cloud SQL, Secret Manager, basic logging and alerting on errors. Replace the temporary access token with a permanent System User token.

### After the MVP

- Photos: media download, Cloud Storage, dashboard display.
- Missed-report rules tied to the reminder schedule.
- Plan-change notifications to trainees.
- Data export and deletion tooling.

### Open ideas (maybe later, not planned yet)

- **Exercises without an ID, mapped on the dashboard** (noted 2026-11-03). An exercise a trainee reports that isn't in the catalog is saved with its name and `exercise_id` null. A dashboard list of those lets the trainer link each name to an existing exercise (it becomes another of its names, an alias) or create a new exercise from it with one click. Either way, the saved entries with that name get the ID too, so they show up in the progress charts. Trainees don't add exercises themselves: the catalog's IDs are permanent and shared, and "pompki diamentowe" / "diamentowe pompki" / "pompki diamond" would become three exercises.
- **Exercises added by trainers belong to them** (noted 2026-11-03): the shared base catalog plus each trainer's own exercises, so one trainer's additions don't appear in other trainers' plans. Needed by the dashboard's "add exercise" as well as by the WhatsApp idea below.
- **Trainer commands over WhatsApp** (noted 2026-11-03), e.g. "yanek dodaj ćwiczenie pompki diamentowe", "yanek zmień Ani przysiad na 65 kg", "yanek jak idzie Basi?". Needs a trainer's own number recognized as a trainer, with its own prompt; a check before adding an exercise ("Dodać jako nowe ćwiczenie czy jako inną nazwę dla Pompek?") so duplicates don't appear; and the per-trainer exercises above. It's a whole "trainer chat", not one command, and "Out of scope for now" still lists trainers chatting with the bot. Build the dashboard versions first and see whether trainers miss doing it from their phone.
