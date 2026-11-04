# Cheat sheet

Run everything from the project folder (`C:\gh\wabot`).

## Simulate a month of reports with the real LLM

Three trainees (Kasia, Tomek, Ola), 15 messages each in October 2026, through the real OpenAI
model. Prints every message, the bot's reply and what was saved, then a calendar per person.

```
uv run python scripts/simulate_month.py                       # all three people (45 OpenAI calls)
uv run python scripts/simulate_month.py --only ola            # one person: kasia, tomek or ola
uv run python scripts/simulate_month.py --only tomek ola      # two of them
uv run python scripts/simulate_month.py --daily-updates-only  # corrections overwrite instead of adding rows
uv run python scripts/simulate_month.py > sim.txt             # save the output to read later
```

- Uses `OPENAI_API_KEY` and `OPENAI_MODEL` from `.env`; one OpenAI call per message.
- Nothing is sent to WhatsApp and `wabot.db` isn't touched.
- Results go to `simulated_month.db`, wiped at the start of every run and kept afterwards.
- The messages are in `scenarios/kasia.yaml`, `scenarios/tomek.yaml` and `scenarios/ola.yaml`. If you change a
  message, update its expected answer in the same file too, or `uv run pytest` fails on it.

## Dashboard

```
bash run_dashboard.sh                  # dashboard on simulated_month.db, dev login, :8000
PORT=8001 bash run_dashboard.sh        # another port
```

If `simulated_month.db` doesn't exist, the script fills it with the correct answers first (no
OpenAI calls). The same by hand: fill the simulation's database, with the real model (above) or
for free with the correct answers, then start uvicorn:

```
uv run python scripts/simulate_month.py --canned                    # no OpenAI calls, same data every time
DATABASE_URL=sqlite:///simulated_month.db DASHBOARD_DEV_LOGIN=true uv run uvicorn wabot.main:app --reload
```

- Then open http://localhost:8000/dashboard and click a trainer: Trener Marek (Kasia, Tomek) or
  Trenerka Ewa (Ola).
- The second line is Git Bash syntax. In PowerShell:
  `$env:DATABASE_URL="sqlite:///simulated_month.db"; $env:DASHBOARD_DEV_LOGIN="true"; uv run uvicorn wabot.main:app --reload`
  (the variables stay set in that window until you close it).
- `DASHBOARD_DEV_LOGIN` signs in without a password: local only, never in production.
- The simulated trainers have every field a real one has (e.g. the email they sign in with);
  `uv run pytest` fails if a new column is left empty by `scenarios/*.yaml`.

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

## Tests and lint

```
uv run pytest                                      # all tests (no real WhatsApp or OpenAI calls)
uv run pytest tests/test_month_scenario.py         # just the month simulation with canned answers
uv run ruff check . && uv run ruff format .
```

## Run the bot

```
bash run.sh                                        # migrate, test message, tunnel + webhook, bot on :8000
uv run python scripts/add_trainee.py --plan plans/example.yaml   # register your number with a plan
```
