# wabot

A WhatsApp assistant for personal trainers. Trainees send short reports (weight, training, diet) to a WhatsApp bot; an LLM (OpenAI) understands them and replies in Polish, and the trainer sees everything on a dashboard.

Planned features:

- tracking trainees' weight measurements
- tracking how trainees stick to their training plan
- tracking how trainees stick to their diet
- limited conversation about the arrangement with the trainer (e.g. "what's my training plan?")
- reminders: the bot asks trainees for a quick report on a schedule
- a dashboard for the trainer with their trainees' data

The full plan, decisions and milestones are in [docs/PLAN.md](docs/PLAN.md). How the code works, flow by flow, is in [docs/CODE_EXPLANATION.md](docs/CODE_EXPLANATION.md).

## Status

- ✅ Milestone 0 – Meta developer app with the free test number
- ✅ Milestone 1 – webhook, signature check, dedup, replies
- ✅ Milestone 2 – LLM replies in Polish; trainees, their plans and their weight and training reports are in the database
- 🚧 Milestone 3 – trainer dashboard
- Milestone 4 – conversation: corrections, clarifications, diet reports

## Stack

Python 3.12+, FastAPI, SQLAlchemy + Alembic (SQLite locally, Postgres in production), Meta WhatsApp Cloud API, OpenAI API (Responses). Tooling: `uv`, `ruff`, `pytest`.

## Local development

### 1. Install

```bash
uv sync
cp .env.example .env    # then fill in the values
```

### 2. Fill in `.env`

| Variable | Where to get it |
|---|---|
| `WHATSAPP_TOKEN` | Meta app → WhatsApp → API Setup → *Generate access token* (expires after ~24 h), or a permanent System User token (see below) |
| `WHATSAPP_PHONE_NUMBER_ID` | API Setup page, next to the test number |
| `WHATSAPP_TEST_RECIPIENT` | Your own number, digits only (e.g. `48123456789`), added as a recipient on API Setup |
| `WHATSAPP_APP_SECRET` | App settings → Basic → App secret |
| `WHATSAPP_APP_ID` | Optional, App settings → Basic → App ID; otherwise looked up via the token |
| `WHATSAPP_VERIFY_TOKEN` | Any random string you choose; you enter the same value in Meta's webhook settings |
| `OPENAI_API_KEY` | platform.openai.com → API keys |
| `OPENAI_MODEL` | Optional, defaults to `gpt-5.6-luna` |
| `HISTORY_MODE` | Optional. `marked` (default): the LLM gets the recent chat with a marker where the new report starts. `new_only`: the code sends only the messages since the bot's last one |
| `DAILY_UPDATES_ONLY` | Optional. `False` (default): every weight and training report is saved as its own row, so a correction ("sorki, 81") adds a row; the newest row of a day is the current value. `True`: one row per day, a newer report overwrites it |
| `HARDCODED_FIRST_MESSAGE` | Optional. `True` makes messages the bot starts come from `src/wabot/utils.py` instead of the LLM – useful without an API key |

`.env` is gitignored; never commit it.

**Permanent token (optional):** business.facebook.com → Settings → Users → System users → Add (Admin) → assign your app and WhatsApp account → Generate token, expiration *Never*, permissions `whatsapp_business_messaging` and `whatsapp_business_management`.

### 3. Register yourself as a trainee

```bash
uv run alembic upgrade head
uv run python scripts/add_trainee.py
```

The bot answers only registered numbers; anyone else gets "Ten numer nie jest zarejestrowany – skontaktuj się ze swoim trenerem". With no options the script registers `WHATSAPP_TEST_RECIPIENT` with a test trainer and the example plan (`plans/example.yaml`: days of exercise IDs from `src/wabot/exercises.yaml`, with the replacements the trainer allows; copy it to write your own). Run it again to change something, e.g. `--plan my_plan.yaml`, `--no-plan`, `--name "Ania"`; `--phone 48123456789` adds another number. Until the dashboard exists, this is how trainees and plans get into the database.

### 4. Run the bot

```bash
bash run.sh
```

This applies database migrations, sends a test template message to your phone (so you know the token works), starts a [cloudflared](https://github.com/cloudflare/cloudflared/releases/latest) quick tunnel, points Meta's webhook at it (`scripts/set_webhook.py`, the same as editing the Callback URL in the Meta dashboard) and runs the bot on http://localhost:8000. Ctrl+C stops the bot and the tunnel.

- cloudflared is looked up on `PATH`, then at `~/tools/cloudflared.exe`; set `CLOUDFLARED=…` for another location. Its output goes to `.cloudflared.log`.
- The tunnel URL changes on every start; `run.sh` re-registers it each time, so there's nothing to copy by hand. Wait for `Webhook set: https://….trycloudflare.com/webhook` in the terminal (up to 2 minutes) before writing to the bot. Messages sent before that go to the old tunnel and are lost.
- `PORT=8001 bash run.sh` uses another port; `TUNNEL=0 bash run.sh` skips the tunnel and leaves Meta's webhook as it is.
- The script finds the app ID through `WHATSAPP_TOKEN`; set `WHATSAPP_APP_ID` (App settings → Basic) to skip that lookup.

### 5. One-time webhook setup in Meta

In the Meta app, open the WhatsApp **Configuration** page (`developers.facebook.com/apps/<APP_ID>/whatsapp-business/wa-settings/`):

1. **Webhook fields**: subscribe to `messages` (not `message_echoes`). `run.sh` does this too.
2. Once per WhatsApp Business Account, subscribe the app to it – otherwise only Meta's *Test* button reaches the bot, not real messages:

   ```bash
   curl -X POST -H "Authorization: Bearer $WHATSAPP_TOKEN" \
     https://graph.facebook.com/v25.0/<WABA_ID>/subscribed_apps
   ```

To set the Callback URL by hand instead (e.g. with your own tunnel): **Webhook → Edit**, Callback URL `https://something.trycloudflare.com/webhook` (note the `/webhook`), Verify token = `WHATSAPP_VERIFY_TOKEN` from `.env`, or run `uv run python scripts/set_webhook.py https://something.trycloudflare.com`.

Then send the bot a message from your phone that mentions it by name, e.g. *"yanek, waga 82,4"*. Messages without "yanek" are stored but not answered.

### Send a message from the bot

```bash
uv run python scripts/send_test_custom_first_message.py            # compose, send and store
uv run python scripts/send_test_custom_first_message.py --dry-run  # only print it
```

The text is composed by the app: the LLM writes it, or `hardcoded_first_message()` in `src/wabot/utils.py` does when `HARDCODED_FIRST_MESSAGE=True`. What the LLM is told to write is in `src/wabot/prompts.yaml`. WhatsApp only delivers such free-form messages within 24 h of your last message to the bot – outside that window Meta accepts the request but drops the message, and the running bot logs error `131047`. Message the bot first.

### Simulate a month of reports

```bash
uv run python scripts/simulate_month.py                       # 3 trainees x 15 reports, October 2026, real model
uv run python scripts/simulate_month.py --only tomek           # one trainee
uv run python scripts/simulate_month.py --daily-updates-only  # one row per day
```

The trainees and their messages are in `scenarios/*.yaml`: Kasia (`plans/example.yaml`), Tomek (`plans/upper_lower.yaml`) and Ola (`plans/beginner_fbw.yaml`). The script sends the messages in time order through the bot's reply flow at simulated times and prints each reply and what was saved. Uses the OpenAI key from `.env` (one call per message) and its own database, `simulated_month.db`, which is recreated on every run (with the same migrations as `wabot.db`) and kept afterwards; the dashboard runs on it. `--canned` gives the correct answers from the scenario files instead of calling OpenAI. Nothing goes to WhatsApp and `wabot.db` isn't touched.

### Dashboard

`bash run_dashboard.sh` does this (filling `simulated_month.db` with `--canned` if it doesn't exist yet):

```bash
uv run python scripts/simulate_month.py --canned   # fill simulated_month.db without OpenAI calls (or run it without --canned: the real model)
DATABASE_URL=sqlite:///simulated_month.db DASHBOARD_DEV_LOGIN=true uv run uvicorn wabot.main:app --reload
```

Open http://localhost:8000/dashboard and pick a trainer (Trener Marek has Kasia and Tomek, Trenerka Ewa has Ola). `DASHBOARD_DEV_LOGIN` signs in without a password, so it's for local use only.

Google sign-in (needs a Google Cloud project, free; it doesn't need to be the production one):

1. console.cloud.google.com → APIs & Services → OAuth consent screen: External, app name "Yanek",
   your email; while it's in "Testing", add each trainer's Google account under Test users.
2. Credentials → Create credentials → OAuth client ID → Web application. Authorized JavaScript
   origins: `http://localhost` and `http://localhost:8000` (later the production URL). No
   redirect URI needed.
3. In `.env`: `GOOGLE_CLIENT_ID=<the client ID>` and `DASHBOARD_SESSION_SECRET=<random>`
   (make one with `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`).
4. Give a trainer your Google email, e.g. on the simulated data Trener Marek (Kasia's trainer):
   `DATABASE_URL=sqlite:///simulated_month.db uv run python scripts/add_trainee.py --phone 48000000001 --trainer-email you@gmail.com`
   (without `DATABASE_URL=...` it changes `wabot.db`). Running the simulation again resets it.

### Tests and lint

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `HTTP 401 … code 190` | Access token expired – generate a new one |
| `HTTP 403 … (#131005) Access denied` | Token was invalidated (regenerated or app settings changed) – generate a new one |
| "The callback URL or verify token couldn't be validated" | Callback URL missing `/webhook`, tunnel not running, or verify token mismatch – check the uvicorn log and `.cloudflared.log` |
| Meta's *Test* button works, real messages don't | App not subscribed to the WhatsApp Business Account (step 5.2) |
| Bot's message accepted but never arrives; bot logs `131047` | Outside the 24 h window – message the bot first |
| Bot doesn't answer and logs no `POST /webhook` | Tunnel down or Callback URL outdated – restart `run.sh` |
| Bot doesn't answer | The message must contain "yanek" (or "yanku", "yanka", …) |
| Bot answers "Ten numer nie jest zarejestrowany" | Your number isn't a trainee yet – run `uv run python scripts/add_trainee.py` |
| Bot doesn't pick up a changed `.env` or `prompts.yaml` | Restart it – both are read at startup (`run.sh` reloads on `prompts.yaml` changes by itself) |
