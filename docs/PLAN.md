# wabot – project plan

A WhatsApp assistant for personal trainers. Trainees report weight, training and diet adherence in free-form Polish messages; Claude turns them into structured records, holds short back-and-forth conversations, and answers questions about the trainee's arrangement with their trainer. Trainers manage everything in a web dashboard.

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
| Photos | Stored and shown on the dashboard; not sent to Claude (after the MVP) |
| Trainer accounts | Created by hand by the operator |

## Trainee onboarding

Because consent is collected on paper, **the trainer registers each trainee in the dashboard** (name, phone number, consent-on-file date). There are no self-service join codes. A message from an unregistered number gets a fixed Polish reply ("Ten numer nie jest zarejestrowany – skontaktuj się ze swoim trenerem") and nothing is stored beyond what deduplication needs.

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

## Claude's role

One Claude call per inbound message, with:

- **Recent history** – the trainee's last messages within a short window (e.g. 24 h, capped at N messages), including the bot's replies and reminders, so clarifications and replies to reminders work.
- **Write tools** – `save_weight`, `save_training`, `save_diet`, `correct_entry`.
- **Read tools** – `get_training_plan`, `get_diet_plan`, `get_trainer_notes`, `get_recent_entries`.
- **The trainee's local date** in the turn, so "wczoraj" / "w poniedziałek" resolve correctly.

The trainee identity is resolved server-side from the sender's phone number and bound to the tools; tools never take a trainee ID from the model, so one trainee can't reach another's data. Claude writes the reply text (in Polish).

## Reminders

- The trainer sets a reminder schedule per trainee in the dashboard (e.g. weigh-in Mon/Thu 8:00, diet check daily 21:00, training check after planned session days).
- Cloud Scheduler calls the service every ~15 min; it finds reminders that are due (in each trainee's timezone) and sends them.
- WhatsApp only allows free-form business messages within 24 h of the trainee's last message. Outside that window, reminders must use **Meta-approved message templates** (category: utility, language: `pl`). Templates are paid per delivered message – check current Meta pricing for Poland.
- Sent reminders are stored in the trainee's chat history so Claude understands the reply.
- Dashboard "missed report" = no matching submission within some time after a reminder or schedule slot.

## Dashboard (trainer only)

- Sign-in: Firebase Auth / Identity Platform (email or Google).
- Trainee list with last report per category and overdue highlighting.
- Trainee page: weight chart, training and diet check-in history, photos.
- Edit: training plan, diet plan, notes (free text), reminder schedule.
- Register trainee: name, phone, consent-on-file date.
- Every query filtered by the signed-in trainer's ID.

## Architecture

| Layer | Choice |
|---|---|
| WhatsApp | Meta WhatsApp Cloud API (direct) |
| Bot + dashboard | Python, FastAPI on Cloud Run (`europe-central2`) |
| Async processing | Webhook verifies signature, stores message (unique `wamid` = dedup), acks 200, enqueues Cloud Task |
| AI | Anthropic Python SDK, tool use |
| Database | Cloud SQL Postgres, SQLAlchemy + Alembic |
| Files | Cloud Storage (photos) |
| Scheduling | Cloud Scheduler → reminders endpoint |
| Secrets | Secret Manager |
| Dashboard UI | Server-rendered Jinja + HTMX, charts with Chart.js |
| Tooling | uv, ruff, pytest; cloudflared/ngrok tunnel for local webhook testing |

### Core data model

`trainers` → `trainees` (phone, name, timezone, consent_on_file_at, training_plan, diet_plan, notes) → `weight_entries`, `training_checkins`, `diet_checkins`, `photos`, `reminder_schedules`, `messages` (inbound and outbound, used as chat history and for dedup). Every trainee-owned row also carries `trainer_id`.

## Out of scope for now

- Trainers chatting with the bot, and alerts or notifications to trainers.
- Self-service trainee sign-up, and consent collection in the app.
- Billing or subscriptions for trainers.
- The bot giving its own training, diet or medical advice.

## MVP simplifications

The goal is the smallest version that a trainer can use with real trainees. Where a question came up, the MVP takes the simplest answer:

- **Trainer accounts** are created by hand by the operator (a small CLI command plus a Firebase Auth user). No sign-up page.
- **Chat history** sent to Claude is the trainee's last 20 messages from the past 24 h.
- **Corrections** apply to the trainee's latest entry of that type only.
- **Missed reports:** the dashboard shows the date of each trainee's last weight, training and diet report, and highlights any older than a few days. There are no rules tied to the reminder schedule.
- **Reminders:** a simple per-trainee schedule (days of the week plus a time) for each report type, with one Meta template per type.
- **Not in the MVP:** photos, notifying trainees about plan changes, and data-retention and export tooling (data is deleted by hand on request).

## Risks

- **Meta business verification and template approval** are slow and outside our control, so start milestone 6 while building the dashboard (milestone 4).
- **Health data under GDPR.** Keep all GCP resources in EU regions, and note that the Anthropic API processes message text (check its data-processing terms and retention). The paper consent should mention both.
- **Parsing mistakes.** Every save is confirmed back to the trainee, and corrections are supported. The dashboard shows the original message next to each entry so the trainer can check it.
- **Cost:** Claude tokens per message and Meta template fees per reminder. Estimate both once message volumes are known.

## Milestones

### MVP

0. **Developer setup** (no business verification needed)
   - Meta developer account (free, uses a personal Facebook login) and a Meta app with the WhatsApp product. Creating the app asks for a business portfolio – just a name, no documents.
   - Use the app's free test number; add your personal number as a test recipient (up to 5) so you can chat with the bot as a trainee.
   - GCP project in `europe-central2`.
1. **Echo bot** – webhook verification, signature check, dedup, reply; tested locally via tunnel.
2. **Submissions** – trainee lookup by phone, database, Claude tools for saving entries, confirmations.
3. **Conversation** – chat history, clarifications, plan questions, corrections.
4. **Dashboard** – trainer login, trainee registration, plan editing, weight chart, check-in history, last-report view.
5. **Reminders** – per-trainee schedules, Meta templates, Cloud Scheduler.
6. **Go-live accounts** – start around milestone 4, since Meta's steps can take days to weeks.
   - Meta business verification (company documents); unverified accounts have low messaging limits.
   - Production bot number: a Polish prepaid SIM (decided – not a personal number, which would lose its WhatsApp account), never registered on WhatsApp. It only has to receive one verification SMS or call – no device stays connected – but keep the number active so it can be re-verified later.
   - Bot display name (Meta reviews it).
   - Submit the Polish reminder templates for approval.
7. **Production** – Cloud Run deploy, Cloud SQL, Secret Manager, basic logging and alerting on errors.

### After the MVP

- Photos: media download, Cloud Storage, dashboard display.
- Missed-report rules tied to the reminder schedule.
- Plan-change notifications to trainees.
- Data export and deletion tooling.
