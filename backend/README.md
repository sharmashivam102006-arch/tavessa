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

## Local demo scripts

The verification and screenshot scripts in `scripts/` (`verify_stage7_live.py`,
`verify_stage8_e2e.py`, `verify_stage9_e2e.py`, `screenshots_stage7.py`) create
their own demo users in-run (owner via the bootstrap CLI, waiter/kitchen via
the admin API) against an ephemeral database. They are not part of pytest.

```bash
cd backend
.venv/bin/python scripts/verify_stage9_e2e.py   # 81 checks + screenshots
.venv/bin/python scripts/verify_stage7_live.py
.venv/bin/python scripts/screenshots_stage7.py  # docs/stage7-screenshots/
```

Demo passwords (Stage 9 closure — never hardcoded):

| Role | Env var |
| --- | --- |
| Owner | `TAVESSA_DEMO_OWNER_PASSWORD` |
| Waiter | `TAVESSA_DEMO_WAITER_PASSWORD` |
| Kitchen | `TAVESSA_DEMO_KITCHEN_PASSWORD` |

When an env var is unset, the script generates a fresh random password for
that run (`scripts/demo_passwords.py`, `secrets.token_urlsafe`) — there is no
hardcoded fallback. Set the vars to reproduce a run, or to share one password
between two scripts: `run_e2e_compose.sh` bootstraps the owner inside the api
container and then runs `verify_stage9_e2e.py --external`, which logs in as
that owner, so the wrapper exports `TAVESSA_DEMO_OWNER_PASSWORD` for both.

The old hardcoded demo values remain in public git history. That is accepted
and documented (see `docs/development-roadmap.md`, Stage 9 closure note) —
history is not rewritten, and no gitleaks allowlist is added for them.

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
