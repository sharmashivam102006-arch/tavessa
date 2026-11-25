#!/usr/bin/env bash
# run_e2e_compose.sh — run the Stage 9 E2E flows against `docker compose`.
#
# *** NOT EXECUTED / NOT PROVEN — READ BEFORE RUNNING ***
# This sandbox has no Docker, so this entrypoint has NEVER been
# run here. It was written from the local E2E script's --external
# mode and is UNPROVEN until someone runs it against a real
# Compose stack. Do not claim the Compose path works until it
# has actually run green end-to-end.
#
# What it assumes (Stage 10 deliverables — none of these exist
# yet in this repo):
# - A docker-compose.yml at the repo root (or $COMPOSE_FILE) with
#   services `api` (FastAPI on :8000), `web` (Vite dev server on
#   :5173, proxying /api and /ws to the api service) and `db`
#   (PostgreSQL). The `api` image must include the backend tree
#   so `python scripts/seed_e2e.py` and `python -m app.auth.bootstrap`
#   run inside it, with DATABASE_URL already set in the container.
# - A FRESH stack: the seed + bootstrap steps below assume an
#   empty database (re-running them against a used stack fails
#   on the existing owner / duplicate tokens).
# - Playwright + Chromium on the HOST running this script (the
#   E2E script drives a real browser from here, not from a
#   container).
#
# What it does:
# 1. `docker compose up -d --build`, then waits for the api
#    /api/v1/health endpoint.
# 2. Bootstraps the E2E owner inside the api container.
# 3. Seeds Zaika + table tokens inside the api container and
#    captures the seed JSON.
# 4. Runs backend/scripts/verify_stage9_e2e.py --external, which
#    runs the SAME checks as the local run (golden path, splits,
#    WS resilience, KDS reconnect, browser flows, owner flows,
#    session-expiry UI) against the Compose URLs — except the
#    backend-down section (it must not kill the Compose stack's
#    backend) and the direct-SQL ticket-expiry sub-check (no DB
#    access from here).
# 5. Leaves the stack RUNNING for inspection; stop it yourself
#    with `docker compose down` (add -v to wipe the database).
#
# Usage:
#   backend/scripts/run_e2e_compose.sh [compose-file]
# Env overrides: COMPOSE_API_BASE (default http://localhost:8000),
#   COMPOSE_WEB_BASE (default http://localhost:5173).
#   TAVESSA_DEMO_OWNER_PASSWORD: owner password used to bootstrap the
#   owner inside the api container. When unset, a fresh random password
#   is generated for this run (never a hardcoded default) and exported
#   so verify_stage9_e2e.py --external logs in with the same value.
#   See backend/README.md, "Local demo scripts".
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_FILE="${1:-${COMPOSE_FILE:-$REPO_DIR/docker-compose.yml}}"
API_BASE="${COMPOSE_API_BASE:-http://localhost:8000}"
WEB_BASE="${COMPOSE_WEB_BASE:-http://localhost:5173}"
SEED_JSON="${TMPDIR:-/tmp}/tavessa-e2e-seed.json"
OWNER_EMAIL="owner@zaika.example"
OWNER_PASSWORD="${TAVESSA_DEMO_OWNER_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')}"
export TAVESSA_DEMO_OWNER_PASSWORD="$OWNER_PASSWORD"

command -v docker >/dev/null || { echo "docker is not installed" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose plugin is not available" >&2; exit 1; }
[ -f "$COMPOSE_FILE" ] || { echo "no compose file at $COMPOSE_FILE (Stage 10)" >&2; exit 1; }

echo "== compose up ($COMPOSE_FILE) =="
docker compose -f "$COMPOSE_FILE" up -d --build

echo "== wait for api health ($API_BASE) =="
for _ in $(seq 1 60); do
  if curl -fsS "$API_BASE/api/v1/health" >/dev/null 2>&1; then
    echo "api is up"
    break
  fi
  sleep 2
done
curl -fsS "$API_BASE/api/v1/health" >/dev/null || { echo "api did not become healthy" >&2; exit 1; }

echo "== bootstrap owner inside api container =="
printf '%s\n%s\n' "$OWNER_PASSWORD" "$OWNER_PASSWORD" | docker compose -f "$COMPOSE_FILE" exec -T api \
  python -m app.auth.bootstrap --restaurant-slug zaika --email "$OWNER_EMAIL"

echo "== seed Zaika + table tokens inside api container =="
docker compose -f "$COMPOSE_FILE" exec -T api python scripts/seed_e2e.py > "$SEED_JSON"
cat "$SEED_JSON"

echo "== run Stage 9 E2E against compose =="
E2E_API_BASE="$API_BASE" E2E_WEB_BASE="$WEB_BASE" E2E_SEED_JSON="$SEED_JSON" \
  "$REPO_DIR/backend/.venv/bin/python" "$REPO_DIR/backend/scripts/verify_stage9_e2e.py" --external

echo
echo "E2E against compose finished. Stack left running; stop with:"
echo "  docker compose -f $COMPOSE_FILE down   # add -v to wipe the database"
