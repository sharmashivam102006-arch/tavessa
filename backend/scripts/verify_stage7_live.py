"""Stage 7 LIVE verification — run manually, not part of pytest.

Boots the real backend (uvicorn) against REAL PostgreSQL (ephemeral
pgserver) and drives the Stage 7 flows over HTTP + WebSocket:

  bootstrap owner (CLI, password via stdin/getpass pipe) -> login
  (wrong password -> generic 401, audited) -> create waiter + kitchen
  users -> role gates live (waiter cannot call /admin) -> real guest
  order -> bill -> Pay by Cash confirmed by the WAITER over JWT ->
  void an unpaid bill (its items become billable again) -> partial
  cash refund -> daily cash report reconciles (gross - refunds =
  net) -> audit log has the events and no secrets -> kitchen WS
  ticket connects, delivers order.created, and cannot be reused.

Usage:  cd backend && .venv/bin/python scripts/verify_stage7_live.py
Exits 0 only if every check passes. The uvicorn server and the
pgserver cluster are stopped/removed afterwards; nothing here
touches the developer's real database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import pytest

pgserver = pytest.importorskip("pgserver")

PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
WS_BASE = f"ws://127.0.0.1:{PORT}"
OWNER_EMAIL = "owner@zaika.example"
OWNER_PASSWORD = "owner-pass-123"
WAITER_EMAIL = "waiter@zaika.example"
WAITER_PASSWORD = "waiter-pass-123"
KITCHEN_EMAIL = "kitchen@zaika.example"
KITCHEN_PASSWORD = "kitchen-pass-123"

CHECKS: list[str] = []

SEED_SNIPPET = """\
import json, sys
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.db.session import get_db  # noqa: F401  (import warms settings)
from app.models import DiningTable, Restaurant
from app.seed.zaika import RESTAURANT_SLUG, seed_zaika
from app.services.table_service import create_table_token
import os
engine = create_engine(os.environ["DATABASE_URL"], poolclass=None)
factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
s = factory()
try:
    seed_zaika(s)
    restaurant = s.execute(select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)).scalars().first()
    tokens = {}
    for number in (5, 6):
        table = s.execute(select(DiningTable).where(
            DiningTable.restaurant_id == restaurant.id, DiningTable.number == number
        )).scalars().first()
        raw, _row = create_table_token(s, table)
        tokens[number] = raw
    out = {"restaurant_id": str(restaurant.id), "tokens": tokens}
finally:
    s.close()
Path = __import__("pathlib").Path
Path(sys.argv[1]).write_text(json.dumps(out))
print(json.dumps(out))
"""


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise AssertionError(f"check failed: {name} {detail}")


def run(cmd: list[str], cwd: Path, env: dict, input_text: str | None = None) -> str:
    result = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, input=input_text
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def main() -> int:
    # NOTE: pgdata lives under ~/.pg-tmp, NOT /tmp — /tmp here is a
    # 512MB tmpfs and a pgserver cluster (~40MB) exhausts it after a
    # few runs.
    os.makedirs(os.path.expanduser("~/.pg-tmp"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tavessa-stage7-live-", dir=os.path.expanduser("~/.pg-tmp"))
    server = pgserver.get_server(os.path.join(tmp, "pg"))
    proc = None
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

        print("== 1. migrate + seed ==")
        run([sys.executable, "-m", "alembic", "upgrade", "head"], BACKEND_DIR, env)
        seed_path = Path(tmp) / "seed.json"
        seed_snippet = Path(tmp) / "seed_stage7.py"
        seed_snippet.write_text(SEED_SNIPPET)
        seed_env = {**env, "PYTHONPATH": str(BACKEND_DIR)}
        run([sys.executable, str(seed_snippet), str(seed_path)], BACKEND_DIR, seed_env)
        seed = json.loads(seed_path.read_text())
        restaurant_id = seed["restaurant_id"]
        token5 = seed["tokens"]["5"]
        token6 = seed["tokens"]["6"]
        check("seeded Zaika + table tokens", bool(restaurant_id and token5 and token6))

        print("== 2. start uvicorn ==")
        log = open(Path(tmp) / "uvicorn.log", "w")
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=BACKEND_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
        )

        import httpx

        # trust_env=False: the sandbox sets NO_PROXY with bracketed
        # IPv6 entries ([::1]) that httpx's URL parser rejects, and
        # no proxy is wanted for localhost anyway.
        client = httpx.Client(base_url=BASE, timeout=10.0, trust_env=False)
        deadline = time.time() + 30
        up = False
        while time.time() < deadline:
            try:
                if client.get("/api/v1/health").status_code == 200:
                    up = True
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.5)
        check("backend answers /api/v1/health", up)
        if not up:
            raise AssertionError(Path(tmp, "uvicorn.log").read_text()[-3000:])

        def auth_headers(token: str) -> dict:
            return {"Authorization": f"Bearer {token}"}

        def login(email: str, password: str):
            return client.post(
                "/api/v1/auth/login", json={"email": email, "password": password}
            )

        print("== 3. bootstrap owner via CLI ==")
        out = run(
            [sys.executable, "-m", "app.auth.bootstrap",
             "--restaurant-slug", "zaika", "--email", OWNER_EMAIL],
            BACKEND_DIR, env,
            input_text=f"{OWNER_PASSWORD}\n{OWNER_PASSWORD}\n",
        )
        check("bootstrap created the owner", "owner" in out.lower(), out.strip()[:200])

        print("== 4. auth + role gates ==")
        bad = login(OWNER_EMAIL, "wrong-password")
        check("wrong password -> generic 401",
              bad.status_code == 401
              and bad.json().get("error", {}).get("message")
              == "Email or password is incorrect.",
              f"{bad.status_code} {bad.text[:160]}")
        resp = login(OWNER_EMAIL, OWNER_PASSWORD)
        check("owner login 200", resp.status_code == 200, resp.text[:200])
        owner = resp.json()["data"]
        owner_token = owner["accessToken"]
        check("owner must_change_password is false",
              owner["user"]["mustChangePassword"] is False)
        owner_h = auth_headers(owner_token)

        def create_user(email, password, role):
            r = client.post(
                "/api/v1/admin/users",
                json={"email": email, "password": password, "role": role},
                headers=owner_h,
            )
            assert r.status_code == 201, r.text
            return r.json()["data"]

        waiter = create_user(WAITER_EMAIL, WAITER_PASSWORD, "waiter")
        kitchen = create_user(KITCHEN_EMAIL, KITCHEN_PASSWORD, "kitchen")
        check("waiter + kitchen users created",
              waiter["mustChangePassword"] is True
              and kitchen["mustChangePassword"] is True)
        waiter_token = login(WAITER_EMAIL, WAITER_PASSWORD).json()["data"]["accessToken"]
        kitchen_token = login(KITCHEN_EMAIL, KITCHEN_PASSWORD).json()["data"]["accessToken"]
        check("waiter/kitchen can log in", bool(waiter_token and kitchen_token))
        r = client.get("/api/v1/admin/users", headers=auth_headers(waiter_token))
        check("waiter cannot list users (403)", r.status_code == 403, str(r.status_code))
        r = client.get("/api/v1/admin/dashboard/overview",
                       params={"restaurant_id": restaurant_id},
                       headers=auth_headers(kitchen_token))
        check("kitchen cannot view dashboard (403)", r.status_code == 403,
              str(r.status_code))

        print("== 5. guest order -> bill -> cash (waiter JWT) ==")
        guest_h = {"X-Table-Token": token5}
        r = client.get("/api/v1/guest/menu", headers=guest_h)
        assert r.status_code == 200, r.text
        naan = next(i for i in r.json()["items"] if i["name"] == "Butter Naan")
        r = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": naan["id"], "quantity": 2}]},
            headers={**guest_h, "Idempotency-Key": f"live-{uuid.uuid4().hex}"},
        )
        check("guest order placed (201)", r.status_code == 201, r.text[:200])
        r = client.post("/api/v1/guest/bill", headers=guest_h)
        assert r.status_code == 200, r.text
        bill = r.json()["data"]
        check("bill issued", bill["status"] == "issued" and bill["totalPaise"] == 9800,
              f"{bill['billNumber']} {bill['totalPaise']}")
        r = client.patch("/api/v1/guest/bill/request-payment", headers=guest_h)
        check("payment requested", r.status_code == 200, r.text[:200])
        r = client.post(
            f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={restaurant_id}",
            json={"amountPaise": bill["totalPaise"]},
            headers={**auth_headers(waiter_token),
                     "Idempotency-Key": f"pay-{uuid.uuid4().hex}"},
        )
        check("waiter confirms cash over JWT (201)", r.status_code == 201, r.text[:200])
        receipt = r.json()["data"]
        check("receipt issued",
              bool(receipt.get("receipt", {}).get("receiptNumber")),
              str(receipt.get("receipt", {}).get("receiptNumber")))
        r = client.get("/api/v1/guest/bill", headers=guest_h)
        check("bill is paid", r.json()["data"]["status"] == "paid",
              r.json()["data"]["status"])

        print("== 6. void an unpaid bill; items billable again ==")
        guest6_h = {"X-Table-Token": token6}
        r = client.post(
            "/api/v1/orders",
            json={"items": [{"menuItemId": naan["id"], "quantity": 1}]},
            headers={**guest6_h, "Idempotency-Key": f"live-{uuid.uuid4().hex}"},
        )
        assert r.status_code == 201, r.text
        r = client.post("/api/v1/guest/bill", headers=guest6_h)
        assert r.status_code == 200, r.text
        bill2 = r.json()["data"]
        r = client.post(f"/api/v1/admin/bills/{bill2['id']}/void",
                        json={"reason": "Guest left"},
                        headers=owner_h)
        check("unpaid bill voided", r.status_code == 200, r.text[:200])
        check("voided bill status", r.json()["data"]["status"] == "voided")
        r = client.post("/api/v1/guest/bill", headers=guest6_h)
        check("voided items billable again (new bill)",
              r.status_code == 200 and r.json()["data"]["status"] == "issued",
              f"{r.status_code} {r.text[:160]}")

        print("== 7. partial cash refund ==")
        payments = client.get(
            "/api/v1/admin/payments", params={"restaurant_id": restaurant_id},
            headers=owner_h).json()["data"]
        payment = next(p for p in payments if p["billId"] == bill["id"])
        check("payment shows the waiter as collector",
              payment.get("collectedByName") == WAITER_EMAIL,
              str(payment.get("collectedByName")))
        r = client.post(
            f"/api/v1/admin/bills/{bill['id']}/refunds",
            json={"paymentId": payment["id"], "amountPaise": 1000,
                  "reason": "Wrong dish"},
            headers={**owner_h, "Idempotency-Key": f"ref-{uuid.uuid4().hex}"},
        )
        check("partial refund recorded (201)", r.status_code == 201, r.text[:200])
        refund = r.json()["data"]
        check("refund reference RFD-…",
              refund["refundReference"].startswith("RFD-"),
              refund["refundReference"])
        r = client.get(f"/api/v1/admin/bills/{bill['id']}",
                       params={"restaurant_id": restaurant_id}, headers=owner_h)
        check("bill partially refunded",
              r.json()["data"]["status"] == "partially_refunded",
              r.json()["data"]["status"])

        print("== 8. daily cash report reconciles ==")
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        r = client.get("/api/v1/admin/reports/daily-cash",
                       params={"restaurant_id": restaurant_id, "date": today},
                       headers=owner_h)
        assert r.status_code == 200, r.text
        report = r.json()["data"]
        gross, refunds, net = (
            report["grossCashCollectedPaise"],
            report["cashRefundsPaise"],
            report["netCashPaise"],
        )
        check("gross - refunds = net",
              gross - refunds == net, f"gross={gross} refunds={refunds} net={net}")
        check("gross is the paid bill total", gross == 9800, str(gross))
        check("refunds are the partial refund", refunds == 1000, str(refunds))
        check("outstanding is the re-billed Table 6 order (4900)",
              report["outstandingPaise"] == 4900, str(report["outstandingPaise"]))

        print("== 9. audit log ==")
        r = client.get("/api/v1/admin/audit-logs",
                       params={"restaurant_id": restaurant_id, "per_page": 100},
                       headers=owner_h)
        assert r.status_code == 200, r.text
        rows = r.json()["data"]
        actions = {row["action"] for row in rows}
        for expected in ("login.failed", "login.success", "user.created",
                         "bill.voided", "refund.created", "payment.recorded"):
            check(f"audit has {expected}", expected in actions, str(sorted(actions)))
        leak = [row for row in rows
                if any(k in json.dumps(row.get("details") or {}).lower()
                       for k in ("password", "token", "secret"))]
        check("no secrets in audit details", not leak, str(leak[:1]))

        print("== 10. WebSocket ticket ==")
        import asyncio
        import websockets
        from websockets.exceptions import ConnectionClosedError

        async def ws_flow():
            # Wrong role: a waiter may not open the kitchen channel.
            r = client.post("/api/v1/ws/tickets", json={"channel": "kitchen"},
                            headers=auth_headers(waiter_token))
            assert r.status_code == 403, r.text
            # Kitchen user gets a ticket.
            r = client.post("/api/v1/ws/tickets", json={"channel": "kitchen"},
                            headers=auth_headers(kitchen_token))
            assert r.status_code == 200, r.text
            ticket = r.json()["data"]["ticket"]
            uri = (f"{WS_BASE}/ws/kitchen?restaurant_id={restaurant_id}"
                   f"&ticket={ticket}")
            received: list[dict] = []

            async with websockets.connect(uri, max_size=2**20) as ws:
                # Trigger an event: a new order on Table 5.
                r = client.post(
                    "/api/v1/orders",
                    json={"items": [{"menuItemId": naan["id"], "quantity": 1}]},
                    headers={**guest_h, "Idempotency-Key": f"ws-{uuid.uuid4().hex}"},
                )
                assert r.status_code == 201, r.text
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    received.append(json.loads(raw))
                except asyncio.TimeoutError:
                    pass
            return received, ticket, uri

        received, ticket, uri = asyncio.run(ws_flow())
        check("kitchen WS connects with a ticket", True)
        check("order.created delivered over WS",
              any(e.get("type") == "order.created" for e in received),
              str(received[:1]))
        # Reusing the same ticket must fail with 4401. The server
        # accepts and then closes with the code, so the close frame
        # is observed on recv, not at handshake.
        from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

        async def reuse():
            try:
                async with websockets.connect(uri, max_size=2**20) as ws:
                    await ws.recv()
                    return None
            except (ConnectionClosedError, ConnectionClosedOK) as exc:
                return exc.code

        code = asyncio.run(reuse())
        check("reused ticket rejected (4401)", code == 4401, f"close code {code}")

        print(f"\nLive verification: {len(CHECKS)} checks passed.")
        return 0
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            server.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
