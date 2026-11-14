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
- Results go to `simulated_month.db`, wiped at the start of every run and kept afterwards
  (a scratch database, separate on purpose: the simulation recreates it). To look at them in the
  dashboard: `DATABASE_URL=sqlite:///simulated_month.db bash run_dashboard.sh`.
- The messages are in `scenarios/kasia.yaml`, `scenarios/tomek.yaml` and `scenarios/ola.yaml`. If you change a
  message, update its expected answer in the same file too, or `uv run pytest` fails on it.

## Database

One setting says which database everything uses – the bot, the dashboard, migrations and every
script: `DATABASE_URL` in `.env` (`sqlite:///wabot.db`). The bot logs it at startup ("Database: ...").

```
uv run python scripts/setup_db.py                       # create / migrate DATABASE_URL's database
uv run python scripts/setup_db.py --simulated           # ... and add the simulated month (Marek, Ewa, Kasia, Tomek, Ola)
uv run python scripts/setup_db.py --new dev.db          # a new empty database; prints the .env line for it
uv run python scripts/setup_db.py --new dev.db --simulated
```

- To switch databases, change `DATABASE_URL` in `.env` and restart `run.sh` / `run_dashboard.sh`.
- Just once, without changing `.env` (Git Bash): `DATABASE_URL=sqlite:///dev.db bash run_dashboard.sh`.
- `--simulated` backs the database up to `db_backups/` first, uses the scenarios' correct answers
  (no OpenAI), and refuses if the simulated trainees are already there.

## Dashboard

```
bash run_dashboard.sh                  # dashboard on DATABASE_URL's database, dev login, :8001
PORT=8002 bash run_dashboard.sh        # another port
```

- Then open http://localhost:8001/dashboard and click a trainer: Trener Marek (Kasia, Tomek),
  Trenerka Ewa (Ola), or your own.
- It uses the same database as the bot (below), so it runs next to `run.sh` (the bot is on :8000).
- `DASHBOARD_DEV_LOGIN` signs in without a password: local only, never in production.
- The simulated trainers have every field a real one has (e.g. the email they sign in with);
  `uv run pytest` fails if a new column is left empty by `scenarios/*.yaml`.

Google sign-in (needs a Google Cloud project, free; it doesn't need to be the production one):

1. console.cloud.google.com → APIs & Services → OAuth consent screen: External, app name "Yanek",
   your email; while it's in "Testing", add each trainer's Google account under Test users.
2. Credentials → Create credentials → OAuth client ID → Web application. Authorized JavaScript
   origins: `http://localhost`, `http://localhost:8000` and `http://localhost:8001` (later the production URL). No
   redirect URI needed.
3. In `.env`: `GOOGLE_CLIENT_ID=<the client ID>` and `DASHBOARD_SESSION_SECRET=<random>`
   (make one with `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`).
4. Give a trainer your Google email, e.g. Trener Marek (Kasia's trainer) of the simulated month:
   `uv run python scripts/add_trainee.py --phone 48000000001 --trainer-email you@gmail.com`
   (in the database `DATABASE_URL` points to).

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
