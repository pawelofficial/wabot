# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Planning stage – there is no code yet. The product plan, decisions and milestones are in `docs/PLAN.md`; read it before doing anything, and keep it updated when decisions change. Don't write code until the user asks to start a milestone. When code exists, add its build/test/lint commands here.

## Key decisions (details in docs/PLAN.md)

- Python, FastAPI on Cloud Run, Cloud SQL Postgres, Cloud Storage, Cloud Tasks, Cloud Scheduler; region `europe-central2`.
- Meta WhatsApp Cloud API directly. Reminders outside the 24 h window need approved templates.
- Multi-trainer: every trainee-owned row carries `trainer_id`; the trainee is resolved server-side from the sender's phone number, and Claude's tools never accept a trainee ID from the model.
- Bot language is Polish. Trainee plans are free text written by the trainer.
- Trainers register their trainees in the dashboard (consent is collected on paper); unknown numbers get a fixed reply.
- Photos are stored and shown on the dashboard, never sent to Claude.
- Planned tooling: `uv`, `ruff`, `pytest`.
