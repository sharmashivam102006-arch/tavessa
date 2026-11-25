"""Load sanity — Stage 9 (informative, NOT a pass/fail gate).

10 concurrent tables ordering against the LOCAL stack only
(ephemeral pgserver + uvicorn on 127.0.0.1). API-level load is
fine for this check: it exercises the real HTTP + Postgres path
(order placement, idempotency claims, row locks, connection
pool) without a browser.

What it measures and prints:
- p50 / p95 / max latency for order placement,
- count of 5xx responses and of 409s (beyond expected
  idempotency conflicts — there are none here, every placement
  uses a fresh key, so any 409 is a finding),
- DB connection behaviour (pool exhaustion / connection errors
  surface as httpx errors and are counted),
- WHAT BREAKS FIRST (the first error class seen, or "nothing
  broke" with the numbers).

After the burst it re-runs one golden-path order + bill: if
that fails, the load broke the golden path and the script exits
1. Otherwise it exits 0 — the numbers are the result, not a
verdict.

NEVER run this against a hosted/production service.

Usage:  cd backend && .venv/bin/python scripts/load_sanity.py
"""
from __future__ import annotations

import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(BACKEND_DIR))

import pytest

pgserver = pytest.importorskip("pgserver")

PORT = 8001  # not 8000: avoids clashing with a dev server
BASE = f"http://127.0.0.1:{PORT}"
TABLES = 10
ORDERS_PER_TABLE = 5
WORKERS = TABLES

LOAD_SEED = """\
import json, sys, os
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.models import DiningTable, Restaurant
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token
engine = create_engine(os.environ["DATABASE_URL"], poolclass=None)
factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
s = factory()
try:
    seed_zaika(s)
    restaurant = s.execute(select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)).scalars().first()
    tokens = {}
    for number in range(1, 11):
        table = s.execute(select(DiningTable).where(
            DiningTable.restaurant_id == restaurant.id, DiningTable.number == number
        )).scalars().first()
        raw, _row = create_table_token(s, table)
        tokens[number] = raw
    out = {"restaurant_id": str(restaurant.id), "tokens": tokens}
finally:
    s.close()
print(json.dumps(out))
"""


def _run(cmd, cwd, env, input_text=None):
    result = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, input=input_text)
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def _wait_http(url, timeout=30):
    import httpx

    client = httpx.Client(timeout=5.0, trust_env=False)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.get(url).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(len(ordered) * pct / 100)))
    return ordered[idx]


def main() -> int:
    import httpx
    import json

    os.makedirs(os.path.expanduser("~/.pg-tmp"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tavessa-stage9-load-", dir=os.path.expanduser("~/.pg-tmp"))
    server = pgserver.get_server(os.path.join(tmp, "pg"))
    uvicorn = None
    try:
        uri = server.get_uri()
        url = uri.replace("postgresql://", "postgresql+psycopg://", 1)
        env = {**os.environ, "DATABASE_URL": url,
               "SECRET_KEY": "test-only-secret", "ENVIRONMENT": "development"}
        print("== migrate + seed 10 tables ==")
        _run([sys.executable, "-m", "alembic", "upgrade", "head"], BACKEND_DIR, env)
        seed_path = Path(tmp) / "load_seed.py"
        seed_path.write_text(LOAD_SEED)
        out = _run([sys.executable, str(seed_path)], BACKEND_DIR,
                   {**env, "PYTHONPATH": str(BACKEND_DIR)})
        seed = json.loads(out.strip().splitlines()[-1])
        tokens = seed["tokens"]
        print(f"seeded {len(tokens)} tables")

        uvicorn_log = open(Path(tmp) / "uvicorn.log", "w")
        uvicorn = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=BACKEND_DIR, env=env, stdout=uvicorn_log, stderr=subprocess.STDOUT,
        )
        assert _wait_http(f"{BASE}/api/v1/health"), "backend did not start"
        print("backend up")

        # Open a session + fetch the menu for every table first
        # (setup, not measured), so the burst is pure placement.
        clients = {}
        menus = {}
        for number, token in tokens.items():
            client = httpx.Client(base_url=BASE, timeout=30.0, trust_env=False)
            r = client.get(f"/api/v1/guest/t/{token}")
            assert r.status_code == 200, r.text
            menus[number] = client.get(
                "/api/v1/guest/menu",
                headers={"X-Table-Token": token}).json()["items"]
            clients[number] = (client, token)
        naan = next(i for i in menus["5"] if i["name"] == "Butter Naan")

        print(f"== burst: {TABLES} tables x {ORDERS_PER_TABLE} orders ==")
        barrier = threading.Barrier(WORKERS)
        latencies: list[float] = []
        statuses: dict[int, int] = {}
        errors: list[str] = []
        lock = threading.Lock()
        first_error = {"at": None, "what": None}
        start = time.time()

        def _one_table(number):
            client, token = clients[number]
            barrier.wait(timeout=30)
            for _ in range(ORDERS_PER_TABLE):
                t0 = time.time()
                try:
                    r = client.post(
                        "/api/v1/orders",
                        json={"items": [{"menuItemId": naan["id"], "quantity": 1}]},
                        headers={"X-Table-Token": token,
                                 "Idempotency-Key": f"load-{uuid.uuid4().hex}"},
                    )
                    status = r.status_code
                except Exception as exc:  # noqa: BLE001 — counted, not raised
                    status = 0
                    with lock:
                        errors.append(f"table {number}: {type(exc).__name__}: {exc}")
                        if first_error["at"] is None:
                            first_error["at"] = time.time() - start
                            first_error["what"] = f"{type(exc).__name__}: {exc}"
                dt = (time.time() - t0) * 1000
                with lock:
                    latencies.append(dt)
                    statuses[status] = statuses.get(status, 0) + 1
                    if status >= 500 and first_error["at"] is None:
                        first_error["at"] = time.time() - start
                        first_error["what"] = f"HTTP {status}"

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(_one_table, [str(n) for n in range(1, 11)]))
        wall = time.time() - start

        total = len(latencies)
        ok = statuses.get(201, 0)
        n_5xx = sum(c for s, c in statuses.items() if s >= 500)
        n_409 = statuses.get(409, 0)
        n_err = len(errors)
        print("\n== LOAD SANITY — 10 concurrent tables ordering (local stack) ==")
        print(f"placements attempted : {total} in {wall:.1f}s wall "
              f"({total / wall:.1f} req/s)")
        print(f"201 Created          : {ok}")
        print(f"5xx                  : {n_5xx}")
        print(f"409 Conflict         : {n_409} (expected 0 — fresh keys)")
        print(f"transport errors     : {n_err} (pool/connection failures land here)")
        print(f"status mix           : {dict(sorted(statuses.items()))}")
        if latencies:
            print(f"placement latency ms : p50={statistics.median(latencies):.0f} "
                  f"p95={_percentile(latencies, 95):.0f} "
                  f"max={max(latencies):.0f}")
        if first_error["at"] is not None:
            print(f"WHAT BROKE FIRST     : {first_error['what']} "
                  f"at {first_error['at']:.1f}s into the burst")
        else:
            print("WHAT BROKE FIRST     : nothing broke — no 5xx, no 409, "
                  "no transport errors in this run")
        for err in errors[:5]:
            print(f"  error: {err}")

        # Golden path still works after the burst?
        print("\n== golden path after load ==")
        client, token = clients["5"]
        r = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": naan["id"], "quantity": 1}]},
            headers={"X-Table-Token": token,
                     "Idempotency-Key": f"load-golden-{uuid.uuid4().hex}"},
        )
        golden_ok = r.status_code == 201
        if golden_ok:
            bill = client.post("/api/v1/guest/bill",
                               headers={"X-Table-Token": token})
            golden_ok = bill.status_code == 200 and bill.json()["data"]["totalPaise"] > 0
        print(f"golden path after load: {'OK' if golden_ok else 'BROKEN'}")
        return 0 if golden_ok else 1
    finally:
        if uvicorn is not None:
            uvicorn.terminate()
            try:
                uvicorn.wait(timeout=10)
            except subprocess.TimeoutExpired:
                uvicorn.kill()
        try:
            server.cleanup()
        except Exception:
            pass
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
