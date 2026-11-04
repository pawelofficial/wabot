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
