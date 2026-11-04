# wabot – project plan

A WhatsApp assistant for personal trainers. Trainees report weight, training and diet adherence in free-form Polish messages; an LLM turns them into structured records, holds short back-and-forth conversations, and answers questions about the trainee's arrangement with their trainer. Trainers manage everything in a web dashboard.

## Decisions

| Topic | Decision |
|---|---|
| Tenancy | Many trainers; each sees only their own trainees |
| Language | Polish (bot replies, reminder templates, dashboard) |
| Trainee plans | Free text per trainee (training plan, diet plan, notes), written by the trainer in the dashboard |
| Reminders | Yes – the bot messages trainees on a schedule |
| Missed reports | Trainer sees them on the dashboard; no alerts |
| Trainer interface | Dashboard only (trainers don't chat with the bot) |
| WhatsApp number | One shared bot number for all trainers; trainees are mapped to their trainer by phone number |
| Consent (GDPR) | Collected on paper, offline; the app only records that it's on file |
| Photos | Stored and shown on the dashboard; not sent to the LLM (after the MVP) |
| Trainer accounts | Created by hand by the operator |

## Trainee onboarding

Because consent is collected on paper, **the trainer registers each trainee in the dashboard** (name, phone number, consent-on-file date). There are no self-service join codes. A message from an unregistered number gets a fixed Polish reply ("Ten numer nie jest zarejestrowany – skontaktuj się ze swoim trenerem") to every message, and nothing is stored beyond what deduplication needs: the message ID, number and type, not the text. Until the dashboard exists, `scripts/add_trainee.py` registers trainees and their trainer (decided 2026-11-03).

## What the bot handles

| Case | Example | Behavior |
|---|---|---|
| Submission | "82,4 rano, nogi zrobione, wieczorem pizza 😬" | Save weight, training and diet entries; confirm what was saved |
| Clarification | "82" → "kg?" → "tak" | Short back-and-forth until the entry is complete |
| Question about the arrangement | "co mam w czwartek?" | Answer only from the trainer's plan text; say so if the plan doesn't cover it |
| Correction | "sorry, 81,4 nie 18,4" | Correct the trainee's most recent matching entry |
| Off-topic / advice | "mogę brać kreatynę?" | Redirect to the trainer; no medical or nutrition advice of its own |
| Reply to a reminder | Reminder: "Jak dziś z dietą?" → "dobrze, bez odstępstw" | Interpreted in context of the reminder (it's part of the chat history) |
| Photo | progress or meal photo | After the MVP: stored in Cloud Storage and shown on the dashboard. In the MVP: a fixed reply that photos aren't supported yet; any caption is processed like a normal message |

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
- **Recent history** – the trainee's last messages within a short window (e.g. 24 h, capped at N messages), including the bot's replies and reminders, so clarifications and replies to reminders work. A "--- NOWE WIADOMOŚCI ---" marker after the bot's last message shows where the new report starts; data is extracted only below it. `HISTORY_MODE=new_only` makes the code drop everything above the marker instead, in case the LLM re-extracts old reports (decided 2026-11-03; less context, so corrections and answers to the bot's questions lose what they refer to).
- **The trainee's local date** after the history, so "wczoraj" / "w poniedziałek" resolve correctly.
- **Structured output** (decided 2026-11-03, replacing the write tools for now) – a JSON schema with `reply` (the Polish WhatsApp text), `weight` (`kg`, `date`) and `training` (`date`, `plan_day` as named in the plan, `status` done/partial/skipped, `exercises` with a catalog `exercise_id`, sets/reps/weight, `replaces` (the ID of the plan exercise a substitute was done instead of) and a free-text `comment`, `notes`), each null when not reported. `missed_exercises` (plan exercise IDs) is not asked of the model: see the next point. The app saves what it gets and only then sends the reply (a failed save sends the apology instead); only data from messages since the bot's last reply is extracted.
- **What isn't reported counts as not done** (decided 2026-11-03). The model only says which exercises were done or replaced and which plan day it was. The code works out the rest. Missed = the plan day's exercises that aren't in the report as done or replaced. Status = `done` when nothing is missed, otherwise `partial` (`skipped` comes from the model). So "zrobiłem tylko klatę" and "skos 4x10" both save the chest exercise as done and the rest as missed. The bot doesn't ask about the rest. It says what it saved and what's missing, and that a full report for the day replaces it. Muscle-group words ("klata", "plecy") mean that day's exercises for the group, so they aren't aliases of single exercises in the catalog.
- **Every report is kept; the newest one per day is the current value** (decided 2026-11-03, replacing "one per day, a newer one overwrites it"). "yanek 80 kg", then "yanek sorki, 81" leaves two weight rows for the day, 80 and 81, and readers (dashboard, reports) take the newest row of each day (highest `id`). Nothing a trainee sent is lost, and the data can be cleaned up or combined in the database later. `DAILY_UPDATES_ONLY=True` switches back to one row per day that a newer report overwrites. For a correction or addition to a training ("przysiad był na 65 kg", "dorzuć plank") the LLM returns the whole session again, copying the rest from the earlier report in the history. That only works while the earlier report is in the history it gets (24 h / 20 messages, and not with `HISTORY_MODE=new_only`); otherwise the newest row of the day has just the corrected part. Showing the LLM the saved entries of the last days fixes that (milestone 3).
- **Messages sent close together** (decided 2026-11-03): when the trainee calls the bot again before the first reply is out ("yanek 80", "yanek sorki, 81"), only the later message is answered. Its history has both, since both come after the bot's last message. An older report also never overwrites an entry saved from a newer message.
- **Exercise catalog** (decided 2026-11-03) – `src/wabot/exercises.yaml` gives every exercise a fixed ID (e.g. `leg_press` for "suwnica", "wypychanie nóg", "leg press"), so progress can be charted per exercise however it's named and across plan changes. The schema only allows catalog IDs. Starts with 22 exercises maintained in the repo; trainers adding their own is a dashboard feature (milestone 4).
- **Training plans are built from the catalog** (decided 2026-11-03, replacing free-text plans). A plan is days of catalog exercise IDs, each with optional sets, reps, weight, a note, and the **replacements the trainer allows** (also IDs), plus free-text notes for everything else (goal, cardio days, remarks). Until the dashboard builds plans from the catalog, they're YAML files (`plans/example.yaml`) registered with `scripts/add_trainee.py`, which rejects unknown IDs; they're stored as JSON on the trainee. Why: sending the whole catalog with every message is mostly noise, but without it the model can't map "siady" or "suwnica" to an ID. The plan's exercises and replacements, with their aliases, are what a report almost always mentions, so only they go into the prompt. The model sets IDs only for those; any other exercise ("pompki zamiast wykroków" when push-ups aren't listed) gets its full Polish name, and the code matches that name against the catalog's names and aliases (null if nothing matches). A replacement the trainer didn't list is still saved, with `replaces`; the trainee can ask the bot what they may do instead of an exercise.
- Read tools (`get_diet_plan`, `get_trainer_notes`, `get_recent_entries`) are still open; the training plan is injected rather than fetched by a tool.

The trainee identity is resolved server-side from the sender's phone number and bound to the request (plan lookup, saving); the model never supplies a trainee ID, so one trainee can't reach another's data. The LLM writes the reply text (in Polish).

## Reminders

- The trainer sets a reminder schedule per trainee in the dashboard (e.g. weigh-in Mon/Thu 8:00, diet check daily 21:00, training check after planned session days).
- Cloud Scheduler calls the service every ~15 min; it finds reminders that are due (in each trainee's timezone) and sends them.
- WhatsApp only allows free-form business messages within 24 h of the trainee's last message. Outside that window, reminders must use **Meta-approved message templates** (category: utility, language: `pl`). Templates are paid per delivered message – check current Meta pricing for Poland.
- Sent reminders are stored in the trainee's chat history so the LLM understands the reply.
- Dashboard "missed report" = no matching submission within some time after a reminder or schedule slot.

## Dashboard (trainer only)

- Sign-in: Firebase Auth / Identity Platform (email or Google).
- Trainee list with last report per category and overdue highlighting.
- Trainee page: weight chart, training calendar (below), training and diet check-in history, photos.
- Edit: training plan (built from the exercise catalog: days, exercises, sets/reps/weight, allowed replacements), diet plan, notes (free text), reminder schedule.
- Register trainee: name, phone, consent-on-file date.
- Exercises: add the trainer's own; map reported exercises that have no ID (see "Open ideas").
- Every query filtered by the signed-in trainer's ID.

### Training calendar (idea, noted 2026-11-03)

A month view per trainee. Each day shows the newest training check-in of that day, labelled with the plan day's short label ("A"). Clicking a day shows the exercises, weights, replacements, notes and the original WhatsApp message.

- **Colour is the saved `status`**, which the code computes from the plan day and the report (see "What isn't reported counts as not done"). Green: `done`, every exercise of that plan day was done or replaced. Amber: `partial`, with `missed_exercises` shown as e.g. "4/5, brak: plank". Grey/red: `skipped` (the trainee said so). Its own colour and an icon: training outside the plan (`plan_day` null, e.g. running). Blank: nothing reported. Sets or weight below the plan don't count at first; "done but lighter" can become its own shade if trainers want it.
- **"Skipped" and "no report" look different.** Skipped is something the trainee said; no report is unknown. Once reminders exist, "no report after a reminder" is the state trainers care about.
- **Two kinds of plan, two meanings of "missed":**
  - Weekday plans (A on Monday, B on Wednesday): every date has an expected session, so a day with no report shows "A planned, no report" (outlined, not filled).
  - Rotation plans (A, B, C in turn, N times a week): no date has an expected session, so the calendar shows only what was done, plus a weekly counter ("2/3 this week"), and maybe a note when the order was broken (B never came between A and C).
- **Plan fields this needs** (none exist yet): `label` on each day (the letter for the cell; it can't reliably be parsed from the name), optional `weekdays` on each day (a plan with them is a weekday plan), and `sessions_per_week` on the plan (for rotations). The dashboard's plan builder offers the two kinds.
- **Open question: two sessions on one day.** With every report kept, a day can have several check-ins, and the calendar takes the newest. But "A in the morning, a run in the evening" is two sessions, not a correction, and newest-wins would hide the A. Possible rules: rows with the same `plan_day` are corrections and different ones are separate sessions; or the LLM's output says whether a report corrects the day's training or adds another session. Decide before the dashboard.

## Architecture

| Layer | Choice |
|---|---|
| WhatsApp | Meta WhatsApp Cloud API (direct) |
| Bot + dashboard | Python, FastAPI on Cloud Run (`europe-central2`) |
| Async processing | Webhook verifies signature, stores message (unique `wamid` = dedup), acks 200, enqueues Cloud Task |
| AI | OpenAI Python SDK (Responses API), tool use; default model `gpt-5.6-luna` (switched from Anthropic on 2026-11-01 – existing OpenAI credit; from `gpt-5.4-mini` to Luna on 2026-11-03 – about 4x cheaper and as good in tests) |
| Database | Cloud SQL Postgres, SQLAlchemy + Alembic |
| Files | Cloud Storage (photos) |
| Scheduling | Cloud Scheduler → reminders endpoint |
| Secrets | Secret Manager |
| Dashboard UI | Server-rendered Jinja + HTMX, charts with Chart.js |
| Tooling | uv, ruff, pytest; cloudflared/ngrok tunnel for local webhook testing |

### Core data model

`trainers` → `trainees` (phone, name, timezone, consent_date, training_plan; diet_plan and notes later) → `weight_entries` (date, kg), `training_checkins` (date, plan_day, status, missed_exercises, notes) with `training_exercises`, later `diet_checkins`, `photos`, `reminder_schedules`; and `messages` (inbound and outbound, used as chat history and for dedup). Every trainee-owned row also carries `trainer_id`; exercise rows are reached only through their check-in, which carries it. Each entry keeps `message_id`, the trainee's message the bot answered when saving it, so the dashboard can show the original next to the entry. Built so far (2026-11-03): trainers, trainees, messages, weight_entries, training_checkins, training_exercises.

## Out of scope for now

- Trainers chatting with the bot (an idea for later, see "Open ideas"), and alerts or notifications to trainers.
- Self-service trainee sign-up, and consent collection in the app.
- Billing or subscriptions for trainers.
- The bot giving its own training, diet or medical advice.

## MVP simplifications

The goal is the smallest version that a trainer can use with real trainees. Where a question came up, the MVP takes the simplest answer:

- **Trainer accounts** are created by hand by the operator (a small CLI command plus a Firebase Auth user). No sign-up page.
- **Chat history** sent to the LLM is the trainee's last 20 messages from the past 24 h.
- **Corrections:** a newer report for the day is the current value; the older one is kept as a row (see "The LLM's role").
- **Missed reports:** the dashboard shows the date of each trainee's last weight, training and diet report, and highlights any older than a few days. There are no rules tied to the reminder schedule.
- **Reminders:** a simple per-trainee schedule (days of the week plus a time) for each report type, with one Meta template per type.
- **Not in the MVP:** photos, notifying trainees about plan changes, and data-retention and export tooling (data is deleted by hand on request).

## Risks

- **Meta business verification and template approval** are slow and outside our control, so start milestone 6 while building the dashboard (milestone 4).
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
2. **Submissions** 🚧 in progress – trainee lookup by phone, database, saving the extracted entries, confirmations.
   - Done (2026-10-29): the LLM (`src/wabot/assistant.py`; OpenAI since 2026-11-01) replies in Polish instead of the echo, using the trainee's last 20 messages from the past 24 h and today's date in Warsaw. Nothing is saved yet besides the messages themselves, so the bot says it "noted" a report. Model is set by `OPENAI_MODEL` (default `gpt-5.6-luna`, low reasoning effort).
   - Done (2026-11-03): the bot answers only when called by name ("yanek"), returns weight and training as JSON next to the reply, and gets the training plan injected into its prompt.
   - Done (2026-11-03): `trainers` and `trainees` tables, the trainee looked up by phone (fixed reply for unknown numbers), each trainee's own plan and timezone from the db, `weight_entries` and `training_checkins` / `training_exercises` filled from the extracted JSON (every report a new row, the newest per day is current; `DAILY_UPDATES_ONLY` overwrites instead), messages linked to their trainee, and `scripts/add_trainee.py` to register a test trainer and trainee.
   - Done (2026-11-03): training plans built from the exercise catalog, with replacements chosen by the trainer; only the plan's exercises go into the prompt (see "The LLM's role").
   - Not done yet: diet reports, which the bot only acknowledges.
3. **Conversation** – chat history, clarifications, plan questions, corrections. Corrections need the LLM to see the trainee's saved entries of the last days (injected, or a `get_recent_entries` tool), so a correction of an older training doesn't replace the day with just the corrected part.
4. **Dashboard** – trainer login, trainee registration, plan editing, weight chart, check-in history, last-report view.
5. **Reminders** – per-trainee schedules, Meta templates, Cloud Scheduler.
6. **Go-live accounts** – start around milestone 4, since Meta's steps can take days to weeks.
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
