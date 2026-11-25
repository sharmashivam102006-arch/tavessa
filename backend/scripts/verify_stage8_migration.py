"""Stage 8 migration verification — run manually, not part of pytest.

Verifies ONE Alembic migration (c8d3e5f6a7b8) on REAL PostgreSQL
(ephemeral pgserver):

  1. alembic upgrade b7c2e9a1d4f5   (Stage 7 head)
  2. Create live data with REAL Stage 7 code (git worktree at
     c21a4bb): seed Zaika + owner login + 5% exclusive tax config +
     guest order -> bill -> equal splits -> payment request -> one
     split paid in cash + a staff WS ticket; snapshot the data.
  3. alembic upgrade head          (Stage 8) — data must survive,
     bill_splits.mode + ws_tickets.table_session_id must exist,
     ws_tickets.user_id must be nullable, the channel CHECK must
     accept 'guest' (a guest ticket row with NULL user_id inserts),
     and the pre-Stage 8 split row must have mode NULL.
  4. alembic downgrade -1          (back to Stage 7) — data must
     survive, the Stage 8 columns must be gone, user_id NOT NULL
     again.
  5. alembic upgrade head          — data must survive again.

Why the worktree: Stage 8's ORM models reference columns the
Stage 7 schema does not have yet, so pre-migration data can only
be created by Stage 7 code. The worktree is removed afterwards.

Usage:  cd backend && .venv/bin/python scripts/verify_stage8_migration.py
Exits 0 only if every check passes. The pgserver cluster is removed
afterwards; nothing here touches the developer's real database.
"""

from __future__ import annotations

import json
import os
import shutil
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

STAGE7_REV = "b7c2e9a1d4f5"
STAGE7_COMMIT = "c21a4bb"
STAGE8_REV = "c8d3e5f6a7b8"
WORKTREE = Path("/tmp/tavessa-stage7-wt")

CHECKS: list[str] = []

# The Stage 7 data-creation helper runs under Stage 7 code, so it
# lives in a file of its own (written to the temp dir at runtime).
STAGE7_HELPER = '''\\
"""Create live Stage 7 data via the Stage 7 HTTP API.

Runs with PYTHONPATH=<worktree>/backend and DATABASE_URL pointing
at the pgserver cluster. Writes a snapshot JSON to the path in
argv[1]. The caller (Stage 8 code) must not import this module —
it only works under Stage 7 code.
"""
import json
import sys
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.rate_limit import limiter
from app.core.security import hash_password
from app.db.session import get_db
from app.main import app
from app.models import Bill, BillSplit, DiningTable, Payment, Restaurant, User
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
    owner = User(
        restaurant_id=restaurant.id,
        branch_id=None,
        email="mig-owner@zaika.example",
        password_hash=hash_password("ownerpass"),
        role="owner",
        is_active=True,
        must_change_password=False,
    )
    setup.add(owner)
    setup.commit()
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
login = client.post(
    "/api/v1/auth/login",
    json={"email": "mig-owner@zaika.example", "password": "ownerpass"},
)
assert login.status_code == 200, login.text
auth = {"Authorization": f"Bearer {login.json()['data']['accessToken']}"}

# 5% exclusive tax, effective now.
now = datetime.now(timezone.utc).isoformat()
tax = client.post(
    "/api/v1/admin/settings/tax",
    json={"enabled": True, "rateBps": 500, "mode": "exclusive",
          "label": "GST", "effectiveFrom": now},
    headers=auth,
)
assert tax.status_code == 201, tax.text

# A staff WS ticket (proves the ws_tickets table is live pre-upgrade).
ticket = client.post(
    "/api/v1/ws/tickets", json={"channel": "staff"}, headers=auth
)
assert ticket.status_code == 200, ticket.text

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
resp = client.post(
    "/api/v1/guest/bill/splits",
    json={"mode": "equal", "parts": 2},
    headers={"X-Table-Token": token},
)
assert resp.status_code == 200, resp.text
resp = client.patch("/api/v1/guest/bill/request-payment", headers={"X-Table-Token": token})
assert resp.status_code == 200, resp.text
frozen = resp.json()["data"]
split = frozen["splits"][0]
resp = client.post(
    f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={restaurant_id}",
    json={"amountPaise": split["amountPaise"], "billSplitId": split["id"]},
    headers={"X-Staff-Dev-Key": "dev-staff-only", "Idempotency-Key": f"pay-{uuid.uuid4().hex}"},
)
assert resp.status_code == 201, resp.text

s = factory()
try:
    from sqlalchemy import func

    from app.auth.models import TaxConfiguration, WsTicket
    from app.models import MenuItem, Order

    bill_row = s.execute(
        select(Bill).where(Bill.bill_number == bill["billNumber"])
    ).scalars().first()
    splits = s.execute(
        select(BillSplit).where(BillSplit.bill_id == bill_row.id)
    ).scalars().all()
    snapshot = {
        "restaurants": s.execute(select(func.count()).select_from(Restaurant)).scalar_one(),
        "menu_items": s.execute(select(func.count()).select_from(MenuItem)).scalar_one(),
        "orders": s.execute(select(func.count()).select_from(Order)).scalar_one(),
        "bills": s.execute(select(func.count()).select_from(Bill)).scalar_one(),
        "payments": s.execute(select(func.count()).select_from(Payment)).scalar_one(),
        "bill_splits": s.execute(select(func.count()).select_from(BillSplit)).scalar_one(),
        "ws_tickets": s.execute(select(func.count()).select_from(WsTicket)).scalar_one(),
        "tax_configs": s.execute(select(func.count()).select_from(TaxConfiguration)).scalar_one(),
        "bill_number": bill_row.bill_number,
        "bill_total": bill_row.total_paise,
        "bill_status": bill_row.status,
        "bill_paid": bill_row.amount_paid_paise,
        "split_amounts": sorted(x.amount_paise for x in splits),
        "split_statuses": sorted(x.status for x in splits),
    }
finally:
    s.close()

Path = __import__("pathlib").Path
Path(sys.argv[1]).write_text(json.dumps(snapshot))
print(f"stage7 data: bill {snapshot['bill_number']} total {snapshot['bill_total']} paise, "
      f"splits {snapshot['split_amounts']}, paid {snapshot['bill_paid']}")
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

    Works at Stage 7 and Stage 8: it only reads columns that exist
    in both revisions, so the same snapshot is comparable across
    upgrade/downgrade/re-upgrade.
    """
    from sqlalchemy import MetaData, func, select

    meta = MetaData()
    meta.reflect(bind=engine)
    bills = meta.tables["bills"]
    splits = meta.tables["bill_splits"]
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
            "bill_splits": conn.execute(
                select(func.count()).select_from(splits)
            ).scalar_one(),
            "ws_tickets": conn.execute(
                select(func.count()).select_from(meta.tables["ws_tickets"])
            ).scalar_one(),
            "tax_configs": conn.execute(
                select(func.count()).select_from(meta.tables["tax_configurations"])
            ).scalar_one(),
            "bill_number": row.bill_number,
            "bill_total": row.total_paise,
            "bill_status": row.status,
            "bill_paid": row.amount_paid_paise,
            "split_amounts": sorted(
                r[0]
                for r in conn.execute(select(splits.c.amount_paise))
            ),
            "split_statuses": sorted(
                r[0] for r in conn.execute(select(splits.c.status))
            ),
        }


def schema_state(engine) -> tuple[set[str], set[str], dict]:
    from sqlalchemy import inspect

    insp = inspect(engine)
    split_cols = {c["name"]: c for c in insp.get_columns("bill_splits")}
    ticket_cols = {c["name"]: c for c in insp.get_columns("ws_tickets")}
    return split_cols, ticket_cols, {
        "ws_ticket_checks": [
            c["name"]
            for c in insp.get_check_constraints("ws_tickets")
        ],
    }


def main() -> int:
    # NOTE: pgdata lives under ~/.pg-tmp, NOT /tmp — /tmp here is a
    # 512MB tmpfs and a pgserver cluster (~40MB) exhausts it after a
    # few runs. The dir is removed by the server cleanup + atexit.
    os.makedirs(os.path.expanduser("~/.pg-tmp"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tavessa-stage8-migration-", dir=os.path.expanduser("~/.pg-tmp"))
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
                ["git", "worktree", "add", "--detach", str(WORKTREE), STAGE7_COMMIT],
                cwd=REPO_DIR,
                env={**os.environ},
            )
            print(f"  worktree created at {WORKTREE} ({STAGE7_COMMIT})")

        from sqlalchemy import create_engine, text

        engine = create_engine(url, poolclass=None)

        print("== 1. alembic upgrade to Stage 7 head ==")
        run([sys.executable, "-m", "alembic", "upgrade", STAGE7_REV], BACKEND_DIR, env)
        current = run([sys.executable, "-m", "alembic", "current"], BACKEND_DIR, env)
        check("at Stage 7 head", STAGE7_REV in current, current)

        print("== 2. live Stage 7 data via Stage 7 code ==")
        helper = Path(tmp) / "stage7_seed_helper.py"
        helper.write_text(STAGE7_HELPER)
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

        print("== 3. alembic upgrade head (Stage 8) ==")
        run([sys.executable, "-m", "alembic", "upgrade", "head"], BACKEND_DIR, env)
        current = run([sys.executable, "-m", "alembic", "current"], BACKEND_DIR, env)
        check("at Stage 8 head", STAGE8_REV in current, current)
        split_cols, ticket_cols, extra = schema_state(engine)
        check("bill_splits.mode exists", "mode" in split_cols)
        check(
            "bill_splits.mode nullable",
            split_cols["mode"]["nullable"] is True,
        )
        check("ws_tickets.table_session_id exists", "table_session_id" in ticket_cols)
        check(
            "ws_tickets.user_id nullable",
            ticket_cols["user_id"]["nullable"] is True,
        )
        check("data survives upgrade", snapshot_core(engine) == before,
              str(snapshot_core(engine)))
        with engine.connect() as conn:
            mode = conn.execute(
                text("SELECT mode FROM bill_splits LIMIT 1")
            ).scalar_one()
            check("pre-Stage 8 split has mode NULL", mode is None, str(mode))
            # The widened channel CHECK must accept a guest ticket
            # with a NULL user — the proof is inserting one.
            conn.execute(
                text(
                    "INSERT INTO ws_tickets (id, user_id, restaurant_id, "
                    "channel, ticket_hash, expires_at, table_session_id) "
                    "SELECT gen_random_uuid(), NULL, restaurants.id, 'guest', "
                    "'mig-test-hash', now() + interval '60 seconds', NULL "
                    "FROM restaurants LIMIT 1"
                )
            )
            conn.commit()
            count = conn.execute(
                text("SELECT count(*) FROM ws_tickets WHERE channel = 'guest'")
            ).scalar_one()
            check("guest ticket row inserts", count == 1, str(count))
            conn.execute(
                text("DELETE FROM ws_tickets WHERE ticket_hash = 'mig-test-hash'")
            )
            conn.commit()

        print("== 4. alembic downgrade -1 (back to Stage 7) ==")
        run([sys.executable, "-m", "alembic", "downgrade", "-1"], BACKEND_DIR, env)
        current = run([sys.executable, "-m", "alembic", "current"], BACKEND_DIR, env)
        check("back at Stage 7 head", STAGE7_REV in current, current)
        split_cols, ticket_cols, _extra = schema_state(engine)
        check("bill_splits.mode gone", "mode" not in split_cols)
        check(
            "ws_tickets.table_session_id gone",
            "table_session_id" not in ticket_cols,
        )
        check(
            "ws_tickets.user_id NOT NULL again",
            ticket_cols["user_id"]["nullable"] is False,
        )
        check("data survives downgrade", snapshot_core(engine) == before,
              str(snapshot_core(engine)))

        print("== 5. alembic upgrade head again ==")
        run([sys.executable, "-m", "alembic", "upgrade", "head"], BACKEND_DIR, env)
        current = run([sys.executable, "-m", "alembic", "current"], BACKEND_DIR, env)
        check("at Stage 8 head again", STAGE8_REV in current, current)
        split_cols, ticket_cols, _extra = schema_state(engine)
        check("bill_splits.mode exists again", "mode" in split_cols)
        check(
            "ws_tickets.table_session_id exists again",
            "table_session_id" in ticket_cols,
        )
        check("data survives re-upgrade", snapshot_core(engine) == before,
              str(snapshot_core(engine)))

        print(f"\nMigration verification: {len(CHECKS)} checks passed.")
        return 0
    finally:
        try:
            server.cleanup()
        except Exception:
            pass
        # Remove the run's own temp dir (seed helper + snapshot);
        # the pgserver cluster inside it is already gone via
        # cleanup().
        shutil.rmtree(tmp, ignore_errors=True)
        # The worktree is a scratch copy of an old commit; remove it
        # so it cannot be mistaken for a second checkout.
        run(
            ["git", "worktree", "remove", "--force", str(WORKTREE)],
            REPO_DIR,
            {**os.environ},
        )


if __name__ == "__main__":
    raise SystemExit(main())
