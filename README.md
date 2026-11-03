# wabot

A WhatsApp assistant for personal trainers. Trainees send short reports (weight, training, diet) to a WhatsApp bot; an LLM (OpenAI) understands them and replies in Polish, and the trainer sees everything on a dashboard.

Planned features:

- tracking trainees' weight measurements
- tracking how trainees stick to their training plan
- tracking how trainees stick to their diet
- limited conversation about the arrangement with the trainer (e.g. "what's my training plan?")
- reminders: the bot asks trainees for a quick report on a schedule
- a dashboard for the trainer with their trainees' data

The full plan, decisions and milestones are in [docs/PLAN.md](docs/PLAN.md).

## Status

- ✅ Milestone 0 – Meta developer app with the free test number
- ✅ Milestone 1 – webhook, signature check, dedup, replies
- 🚧 Milestone 2 – LLM replies in Polish from the recent chat history; saving reports to the database is next

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
| `WHATSAPP_VERIFY_TOKEN` | Any random string you choose; you enter the same value in Meta's webhook settings |
| `OPENAI_API_KEY` | platform.openai.com → API keys |
| `OPENAI_MODEL` | Optional, defaults to `gpt-5.4-mini` |
| `HARDCODED_FIRST_MESSAGE` | Optional. `True` makes messages the bot starts come from `src/wabot/utils.py` instead of the LLM – useful without an API key |

`.env` is gitignored; never commit it.

**Permanent token (optional):** business.facebook.com → Settings → Users → System users → Add (Admin) → assign your app and WhatsApp account → Generate token, expiration *Never*, permissions `whatsapp_business_messaging` and `whatsapp_business_management`.

### 3. Run the bot

```bash
bash run.sh
```

This applies database migrations, sends a test template message to your phone (so you know the token works), and starts the bot on http://localhost:8000.

### 4. Expose it to Meta with a tunnel

Download [cloudflared](https://github.com/cloudflare/cloudflared/releases/latest) (a single `.exe` on Windows) and run in a second terminal:

```bash
cloudflared tunnel --url http://localhost:8000
```

It prints a URL like `https://something.trycloudflare.com`. The URL changes every time cloudflared restarts.

### 5. Connect Meta's webhook

In the Meta app, open the WhatsApp **Configuration** page (`developers.facebook.com/apps/<APP_ID>/whatsapp-business/wa-settings/`):

1. **Webhook → Edit**: Callback URL `https://something.trycloudflare.com/webhook` (note the `/webhook`), Verify token = `WHATSAPP_VERIFY_TOKEN` from `.env` → *Verify and save*.
2. **Webhook fields**: subscribe to `messages` (not `message_echoes`).
3. Once per WhatsApp Business Account, subscribe the app to it – otherwise only Meta's *Test* button reaches the bot, not real messages:

   ```bash
   curl -X POST -H "Authorization: Bearer $WHATSAPP_TOKEN" \
     https://graph.facebook.com/v25.0/<WABA_ID>/subscribed_apps
   ```

Then send the bot a message from your phone.

### Send a message from the bot

```bash
uv run python scripts/send_test_custom_first_message.py            # compose, send and store
uv run python scripts/send_test_custom_first_message.py --dry-run  # only print it
```

The text is composed by the app: the LLM writes it, or `hardcoded_first_message()` in `src/wabot/utils.py` does when `HARDCODED_FIRST_MESSAGE=True`. What the LLM is told to write is in `src/wabot/prompts.yaml`. WhatsApp only delivers such free-form messages within 24 h of your last message to the bot – outside that window Meta accepts the request but drops the message, and the running bot logs error `131047`. Message the bot first.

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
| "The callback URL or verify token couldn't be validated" | Callback URL missing `/webhook`, tunnel not running, or verify token mismatch – check the uvicorn log |
| Meta's *Test* button works, real messages don't | App not subscribed to the WhatsApp Business Account (step 5.3) |
| Bot's message accepted but never arrives; bot logs `131047` | Outside the 24 h window – message the bot first |
| Bot doesn't pick up a changed `.env` or `prompts.yaml` | Restart it – both are read at startup (`run.sh` reloads on `prompts.yaml` changes by itself) |
