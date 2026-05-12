# Musiql

A music streaming service with a built-in recommendation engine.

## Summary 

Musiql lets you build and stream your own music library from a clean web interface. Bring your own audio files, organize your collection, and stream it from anywhere — no subscriptions, no ads, no algorithmic interference you didn't ask for.

The recommendation system is a per-user Markov chain model (GAMP) that uses reinforcment learning and adapts to listening patterns. It builds a personal queue based on what you actually play, skip, and revisit. The more you use it, the better it gets at predicting what you want to hear next.

## Features

- Stream your personal library from anywhere via a web or desktop client
- Automatic queue generation powered by a custom graph-based recommendation model
- Simple, fast UI — search, play, manage your library, that's it
- Add music to your library and manage your collection
- Per-user listening history and library management
- Self-hosted on AWS (Lambda + S3 + RDS) or run locally

## Stack

- **Backend** — Stateless FastAPI, AWS RDS PostgreSQL, AWS Lambda + S3
- **Frontend** — Svelte
- **Recommendations** — GAMP (per-user Markov process over a weighted directed graph of your listening history)

## Self-hosting

Clone with submodules:

```bash
git clone --recurse-submodules https://github.com/vx59/musiql
```

Copy `.env.example` to `.env`, fill in your credentials, then:

```bash
uv sync
uvicorn musiql.server:app --host 0.0.0.0 --port 8000
```

Infrastructure is managed via Terraform in `musiql-terraform-aws-iac/`.

### Database

For a local setup, spin up a PostgreSQL instance and point your `.env` at it, then run the Alembic migrations to create the schema:

```bash
uv run alembic upgrade head
```

To create a new migration after changing a model:

```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```