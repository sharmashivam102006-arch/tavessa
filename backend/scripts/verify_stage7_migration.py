"""Stage 7 migration verification — run manually, not part of pytest.

Verifies ONE Alembic migration (b7c2e9a1d4f5) on REAL PostgreSQL
(ephemeral pgserver):

  1. alembic upgrade a0f6368eca02   (Stage 6 head)
  2. Create live data with REAL Stage 6 code (git worktree at
     074bab5): seed Zaika + drive a guest order -> bill -> payment
     through the HTTP API, and snapshot the data.
  3. alembic upgrade head          (Stage 7) — data must survive,
     new tables/columns must exist.
  4. alembic downgrade -1          (back to Stage 6) — data must
     survive, Stage 7 tables/columns must be gone.
  5. alembic upgrade head          — data must survive again.

Why the worktree: Stage 7's ORM models reference columns the
Stage 6 schema does not have yet, so pre-migration data can only
be created by Stage 6 code. The worktree is removed afterwards.

Usage:  cd backend && .venv/bin/python scripts/verify_stage7_migration.py
Exits 0 only if every check passes. The pgserver cluster is removed
afterwards; nothing here touches the developer's real database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

import pytest

pgserver = pytest.importorskip("pgserver")

STAGE6_REV = "a0f6368eca02"
STAGE6_COMMIT = "074bab5"
STAGE7_REV = "b7c2e9a1d4f5"
WORKTREE = Path("/tmp/tavessa-stage6-wt")
NEW_TABLES = {
    "users",
    "refresh_sessions",
    "ws_tickets",
    "tax_configurations",
    "refunds",
    "audit_logs",
}
NEW_BILL_COLUMNS = {
    "voided_at",
    "void_reason",
    "voided_by",
    "tax_rate_bps_snapshot",
    "tax_mode_snapshot",
    "tax_label_snapshot",
}

CHECKS: list[str] = []

# The Stage 6 data-creation helper runs under Stage 6 code, so it
# lives in a file of its own (written to the temp dir at runtime).
STAGE6_HELPER = '''\
"""Create live Stage 6 data via the Stage 6 HTTP API.

Runs with PYTHONPATH=<worktree>/backend and DATABASE_URL pointing
at the pgserver cluster. Writes a snapshot JSON to the path in
argv[1]. The caller (Stage 7 code) must not import this module —
it only works under Stage 6 code.
"""
import json
import sys
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.rate_limit import limiter
from app.db.session import get_db
from app.main import app
from app.models import Bill, DiningTable, Restaurant
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token

url = __import__("os").environ["DATABASE_URL"]
engine = create_engine(url, poolclass=None)
factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _override_get_db():
    session = factory()
    try:
        yield session
    finally:
        session.close()


app.dependency_overrides[get_db] = _override_get_db
limiter.enabled = False

setup = factory()
try:
    seed_zaika(setup)
    restaurant = setup.execute(
        select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)
    ).scalars().first()
    table5 = setup.execute(
        select(DiningTable).where(
            DiningTable.restaurant_id == restaurant.id,
            DiningTable.number == 5,
        )
    ).scalars().first()
    token, _row = create_table_token(setup, table5)
    restaurant_id = str(restaurant.id)
finally:
    setup.close()

client = TestClient(app)
resp = client.get(f"/api/v1/guest/t/{token}")
assert resp.status_code == 200, resp.text
menu = client.get("/api/v1/guest/menu", headers={"X-Table-Token": token}).json()
naan = next(i for i in menu["items"] if i["name"] == "Butter Naan")
resp = client.post(
    "/api/v1/orders",
    json={"items": [{"menuItemId": naan["id"], "quantity": 2}]},
    headers={"X-Table-Token": token, "Idempotency-Key": f"mig-{uuid.uuid4().hex}"},
)
assert resp.status_code == 201, resp.text
resp = client.post("/api/v1/guest/bill", headers={"X-Table-Token": token})
assert resp.status_code == 200, resp.text
bill = resp.json()["data"]
resp = client.patch("/api/v1/guest/bill/request-payment", headers={"X-Table-Token": token})
assert resp.status_code == 200, resp.text
resp = client.post(
    f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={restaurant_id}",
    json={"amountPaise": bill["totalPaise"]},
    headers={"X-Staff-Dev-Key": "dev-staff-only", "Idempotency-Key": f"pay-{uuid.uuid4().hex}"},
)
assert resp.status_code == 201, resp.text

s = factory()
try:
    from sqlalchemy import func

    from app.models import MenuItem, Order, Payment

    bill_row = s.execute(
        select(Bill).where(Bill.bill_number == bill["billNumber"])
    ).scalars().first()
    snapshot = {
        "restaurants": s.execute(select(func.count()).select_from(Restaurant)).scalar_one(),
        "menu_items": s.execute(select(func.count()).select_from(MenuItem)).scalar_one(),
        "orders": s.execute(select(func.count()).select_from(Order)).scalar_one(),
        "bills": s.execute(select(func.count()).select_from(Bill)).scalar_one(),
        "payments": s.execute(select(func.count()).select_from(Payment)).scalar_one(),
        "bill_number": bill_row.bill_number,
        "bill_total": bill_row.total_paise,
        "bill_status": bill_row.status,
        "bill_paid": bill_row.amount_paid_paise,
    }
finally:
    s.close()

Path = __import__("pathlib").Path
Path(sys.argv[1]).write_text(json.dumps(snapshot))
print(f"stage6 data: bill {snapshot['bill_number']} total {snapshot['bill_total']} paise, paid in full")
print(f"snapshot: {snapshot}")
'''


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise AssertionError(f"check failed: {name} {detail}")


def run(cmd: list[str], cwd: Path, env: dict) -> str:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed:\n{result.stdout}\n{result.stderr}"
        )
    return (result.stdout + result.stderr).strip()


def snapshot_core(engine) -> dict:
    """Schema-version-agnostic snapshot via reflected tables.

    Works at Stage 6 and Stage 7: it only reads columns that exist
    in both revisions, so the same snapshot is comparable across
    upgrade/downgrade/re-upgrade.
    """
    from sqlalchemy import MetaData, func, select

    meta = MetaData()
    meta.reflect(bind=engine)
    bills = meta.tables["bills"]
    with engine.connect() as conn:
        row = conn.execute(
            select(
                bills.c.bill_number,
                bills.c.total_paise,
                bills.c.status,
                bills.c.amount_paid_paise,
            ).order_by(bills.c.bill_number)
        ).first()
        return {
            "restaurants": conn.execute(
                select(func.count()).select_from(meta.tables["restaurants"])
            ).scalar_one(),
            "menu_items": conn.execute(
                select(func.count()).select_from(meta.tables["menu_items"])
            ).scalar_one(),
            "orders": conn.execute(
                select(func.count()).select_from(meta.tables["orders"])
            ).scalar_one(),
            "bills": conn.execute(
                select(func.count()).select_from(bills)
            ).scalar_one(),
            "payments": conn.execute(
                select(func.count()).select_from(meta.tables["payments"])
            ).scalar_one(),
            "bill_number": row.bill_number,
            "bill_total": row.total_paise,
            "bill_status": row.status,
            "bill_paid": row.amount_paid_paise,
        }


def schema_state(engine) -> tuple[set[str], set[str]]:
    from sqlalchemy import inspect

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    bill_cols = {c["name"] for c in insp.get_columns("bills")}
    return tables, bill_cols


def main() -> int:
    # NOTE: pgdata lives under ~/.pg-tmp, NOT /tmp — /tmp here is a
    # 512MB tmpfs and a pgserver cluster (~40MB) exhausts it after a
    # few runs. The dir is removed by the server cleanup + atexit.
    os.makedirs(os.path.expanduser("~/.pg-tmp"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tavessa-stage7-migration-", dir=os.path.expanduser("~/.pg-tmp"))
    server = pgserver.get_server(os.path.join(tmp, "pg"))
    try:
        uri = server.get_uri()
        assert uri.startswith("postgresql://"), uri
        url = uri.replace("postgresql://", "postgresql+psycopg://", 1)
        env = {
            **os.environ,
            "DATABASE_URL": url,
            "SECRET_KEY": "test-only-secret",
            "ENVIRONMENT": "development",
        }

        if not (WORKTREE / "backend" / "app" / "main.py").exists():
            run(
                ["git", "worktree", "add", "--detach", str(WORKTREE), STAGE6_COMMIT],
                cwd=REPO_DIR,
                env={**os.environ},
            )
            print(f"  worktree created at {WORKTREE} ({STAGE6_COMMIT})")

        from sqlalchemy import create_engine, text

        engine = create_engine(url, poolclass=None)

        print("== 1. alembic upgrade to Stage 6 head ==")
        run([sys.executable, "-m", "alembic", "upgrade", STAGE6_REV], BACKEND_DIR, env)
        current = run([sys.executable, "-m", "alembic", "current"], BACKEND_DIR, env)
        check("at Stage 6 head", STAGE6_REV in current, current)

        print("== 2. live Stage 6 data via Stage 6 code ==")
        helper = Path(tmp) / "stage6_seed_helper.py"
        helper.write_text(STAGE6_HELPER)
        snapshot_path = Path(tmp) / "snapshot.json"
        helper_env = {
            **env,
            "PYTHONPATH": str(WORKTREE / "backend"),
        }
        out = run(
            [sys.executable, str(helper), str(snapshot_path)],
            WORKTREE / "backend",
            helper_env,
        )
        print("  " + out.replace("\n", "\n  "))
        before = json.loads(snapshot_path.read_text())
        core_before = snapshot_core(engine)
        check("core snapshot matches helper snapshot", core_before == before,
              f"{core_before} vs {before}")

        print("== 3. alembic upgrade head (Stage 7) ==")
        run([sys.executable, "-m", "alembic", "upgrade", "head"], BACKEND_DIR, env)
        current = run([sys.executable, "-m", "alembic", "current"], BACKEND_DIR, env)
        check("at Stage 7 head", STAGE7_REV in current, current)
        tables, bill_cols = schema_state(engine)
        check("Stage 7 tables exist", NEW_TABLES <= tables, sorted(NEW_TABLES - tables))
        check("bill void/tax columns exist", NEW_BILL_COLUMNS <= bill_cols,
              sorted(NEW_BILL_COLUMNS - bill_cols))
        check("data survives upgrade", snapshot_core(engine) == before,
              str(snapshot_core(engine)))
        with engine.connect() as conn:
            tz = conn.execute(text("SELECT timezone FROM branches LIMIT 1")).scalar_one()
        check("seeded branch timezone default", tz == "Asia/Kolkata", str(tz))

        print("== 4. alembic downgrade -1 (back to Stage 6) ==")
        run([sys.executable, "-m", "alembic", "downgrade", "-1"], BACKEND_DIR, env)
        current = run([sys.executable, "-m", "alembic", "current"], BACKEND_DIR, env)
        check("back at Stage 6 head", STAGE6_REV in current, current)
        tables, bill_cols = schema_state(engine)
        check("Stage 7 tables gone", not (NEW_TABLES & tables), sorted(NEW_TABLES & tables))
        check("bill void/tax columns gone", not (NEW_BILL_COLUMNS & bill_cols),
              sorted(NEW_BILL_COLUMNS & bill_cols))
        check("data survives downgrade", snapshot_core(engine) == before,
              str(snapshot_core(engine)))

        print("== 5. alembic upgrade head again ==")
        run([sys.executable, "-m", "alembic", "upgrade", "head"], BACKEND_DIR, env)
        current = run([sys.executable, "-m", "alembic", "current"], BACKEND_DIR, env)
        check("at Stage 7 head again", STAGE7_REV in current, current)
        tables, bill_cols = schema_state(engine)
        check("Stage 7 tables exist again", NEW_TABLES <= tables)
        check("data survives re-upgrade", snapshot_core(engine) == before,
              str(snapshot_core(engine)))

        print(f"\nMigration verification: {len(CHECKS)} checks passed.")
        return 0
    finally:
        try:
            server.cleanup()
        except Exception:
            pass
        # The worktree is a scratch copy of an old commit; remove it
        # so it cannot be mistaken for a second checkout.
        run(
            ["git", "worktree", "remove", "--force", str(WORKTREE)],
            REPO_DIR,
            {**os.environ},
        )


if __name__ == "__main__":
    raise SystemExit(main())
