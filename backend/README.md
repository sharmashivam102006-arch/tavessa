# Tavessa backend

FastAPI modular monolith — Python 3.12+, SQLAlchemy 2.0, PostgreSQL, Alembic.
See the repo-root `README.md` and `docs/architecture.md` for the design.

## Setup (no Docker)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # edit DATABASE_URL / SECRET_KEY if needed
```

## Run

```bash
# from backend/ — needs PostgreSQL running (see repo-root README, "Database")
uvicorn app.main:app --reload --port 8000
# Health:  curl http://localhost:8000/api/v1/health
# Docs:    http://localhost:8000/docs   (dev only)
```

## Database

PostgreSQL via `DATABASE_URL` (default:
`postgresql+psycopg://tavessa:tavessa@localhost:5432/tavessa`).
Start it with Docker Compose from the repo root (`docker compose up -d db`)
or a local PostgreSQL install, then create the database:

```bash
createdb tavessa   # or: psql -c "CREATE DATABASE tavessa;"
alembic upgrade head
```

Migrations live in `alembic/versions/`. No schema change without a migration.
Stage 3 ships one migration (`9cd1cf3c6050`): the guest domain tables
(restaurants, branches, dining tables, QR tokens, sessions, menu,
orders, help calls, idempotency keys).

## Seed & demo tokens (Stage 3)

```bash
python -m app.seed.zaika                  # idempotent Zaika seed (safe to re-run)
python -m app.seed.demo_tokens --table 5   # dev-only QR token; prints the guest
                                           # URL once; refuses in production
```

## Tests

```bash
pytest -q
```

Tests run against in-memory SQLite (the one exception to the
Postgres-only rule) so the suite runs anywhere. Stage 3: 37 passed /
1 skipped. Postgres-specific tests arrive with the business schema in Stage 3.

`tests/test_postgres_smoke.py` is skipped by default; it verifies a real
Postgres with `SELECT 1` and needs the `pgserver` package
(`pip install pgserver`) plus `TAVESSA_TEST_POSTGRES=1`. It is a
verification aid for this sandbox (no system Postgres/Docker here), not
part of the shipped test suite.
