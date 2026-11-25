"""Stage 9 END-TO-END verification — run manually, not part of pytest.

Boots the real backend (uvicorn :8000) against REAL PostgreSQL
(ephemeral pgserver) plus the real Vite dev server (:5173), then
verifies the complete guest -> kitchen -> waiter -> billing loop
over real WebSocket connections and in real browser contexts.
This is the Stage 8 script extended (same golden path, same
sections A–E), plus the Stage 9 additions:

  F. Owner flows (browser): owner logs in -> /admin overview
     loads with real numbers -> tax settings view shows the
     configured GST -> a menu price change via the API shows up
     on /admin/menu.
  G. KDS offline/reconnect (WS-level): the kitchen socket is
     killed mid-service, reconnects with a fresh ticket, the
     queue is re-fetched from REST (order present exactly once,
     no loss, no duplicates), and a later order arrives exactly
     once on the new socket.
  H. Guest session-expiry UI (browser): the table-8 token is
     revoked mid-flow; reloading the guest page shows "This table
     session has expired" and no menu — it settles there instead
     of looping or showing stale success.

  A. WS golden path (21 steps, brief §13): guest opens the QR menu
     -> places an order -> kitchen sees it live -> preparing -> guest
     sees preparing -> ready -> guest sees ready -> waiter serves ->
     guest sees served -> help request -> staff receives -> ack ->
     guest sees ack -> resolve -> guest sees resolved -> bill request
     -> staff receives -> cash collected -> guest sees updated
     payment/outstanding -> fully paid -> guest views receipt -> an
     unrelated table receives NONE of these events.
  B. Split-bill correctness with tax configured: 5% exclusive tax,
     splits created, tax changed to 10%, bill re-fetched, freeze
     re-derives the equal splits to the frozen total exactly, an
     audit row (bill.splits_reconciled) is written, and a split
     payment then works against the reconciled amounts.
  C. WS resilience: guest disconnect/reconnect with a fresh ticket,
     expired-ticket recovery (4401 then a fresh ticket connects),
     invalid ticket rejection (4401), guest ticket to /ws/kitchen
     (4403), duplicate order/payment replays (no second event).
  D. Browser flow (Playwright, separate contexts for guest, kitchen
     and staff): the guest drives the menu -> cart -> review -> place
     order -> live tracking -> help -> bill -> receipt in the UI
     while the kitchen and staff contexts watch the same order live;
     screenshots land in docs/stage9-screenshots/.
  E. Backend unavailable: uvicorn is killed mid-flow and the guest
     UI must show "Not connected to the backend yet" — never a false
     success state. (Local mode only; skipped with --external.)

Usage:  cd backend && .venv/bin/python scripts/verify_stage9_e2e.py
        [--external]
Exits 0 only if every check passes. Servers and the pgserver cluster
are stopped/removed afterwards; nothing here touches the developer's
real database. Requires: playwright + a Chromium binary in
~/.cache/ms-playwright (already present).

--external (used by scripts/run_e2e_compose.sh): do NOT boot
pgserver/uvicorn/vite here — run the same checks against an
already-running stack (e.g. `docker compose up`). Reads
E2E_API_BASE (default http://127.0.0.1:8000), E2E_WEB_BASE
(default http://127.0.0.1:5173) and E2E_SEED_JSON (seed output
from scripts/seed_e2e.py, run inside the stack). Section E is
skipped (the script must not kill the Compose stack's backend)
and the direct-SQL ticket-expiry sub-check in section C is
skipped (no DB access from here).

Demo passwords: read from TAVESSA_DEMO_*_PASSWORD env vars when set,
otherwise generated fresh per run (scripts/demo_passwords.py) — never
hardcoded. In --external mode the owner password must match the one
run_e2e_compose.sh used to bootstrap the owner, so the wrapper exports
TAVESSA_DEMO_OWNER_PASSWORD. See backend/README.md, "Local demo scripts".
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
FRONTEND_DIR = REPO_DIR / "frontend"
SHOTS_DIR = REPO_DIR / "docs" / "stage9-screenshots"
CHROME = Path.home() / ".cache" / "ms-playwright" / "chromium-1255" / "chrome-linux64" / "chrome"

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_passwords import ENV_KITCHEN, ENV_OWNER, ENV_WAITER, demo_password

import pytest

pgserver = pytest.importorskip("pgserver")

EXTERNAL = "--external" in sys.argv
PORT = 8000  # Vite proxies /api and /ws to localhost:8000 (vite.config.ts).
BASE = os.environ.get("E2E_API_BASE", f"http://127.0.0.1:{PORT}") if EXTERNAL else f"http://127.0.0.1:{PORT}"
WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")
VITE = os.environ.get("E2E_WEB_BASE", "http://127.0.0.1:5173") if EXTERNAL else "http://127.0.0.1:5173"
OWNER_EMAIL = "owner@zaika.example"
OWNER_PASSWORD = demo_password(ENV_OWNER)
WAITER_EMAIL = "waiter@zaika.example"
WAITER_PASSWORD = demo_password(ENV_WAITER)
KITCHEN_EMAIL = "kitchen@zaika.example"
KITCHEN_PASSWORD = demo_password(ENV_KITCHEN)

CHECKS: list[str] = []

# Seeding lives in scripts/seed_e2e.py (shared by local + Compose
# runs); the token-rotation helper below mints one fresh token for
# a single table (used by the session-expiry UI check).
ROTATE_SNIPPET = """\\
import json, sys, os
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.models import DiningTable, Restaurant
from app.seed.zaika import RESTAURANT_SLUG
from app.services.table_service import create_table_token
engine = create_engine(os.environ["DATABASE_URL"], poolclass=None)
factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
s = factory()
try:
    restaurant = s.execute(select(Restaurant).where(Restaurant.slug == RESTAURANT_SLUG)).scalars().first()
    table = s.execute(select(DiningTable).where(
        DiningTable.restaurant_id == restaurant.id, DiningTable.number == int(sys.argv[1])
    )).scalars().first()
    raw, _row = create_table_token(s, table)
    print(raw)
finally:
    s.close()
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


def wait_http(url: str, timeout: int = 30) -> bool:
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


# ----------------------------------------------------------------------
# WebSocket collector: one background reader per socket, events kept in
# a list so the test can assert delivery, order and absence.
# ----------------------------------------------------------------------
class Collector:
    def __init__(self, label: str) -> None:
        self.label = label
        self.events: list[dict] = []

    async def run(self, ws) -> None:
        import websockets

        try:
            async for raw in ws:
                self.events.append(json.loads(raw))
        except websockets.exceptions.ConnectionClosed:
            pass

    def of_type(self, type_: str) -> list[dict]:
        return [e for e in self.events if e.get("type") == type_]

    async def wait_for(self, pred, timeout: float = 10.0) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for event in self.events:
                if pred(event):
                    return event
            await asyncio.sleep(0.1)
        return None

    async def wait_type(self, type_: str, timeout: float = 10.0, **match) -> dict | None:
        def pred(e: dict) -> bool:
            if e.get("type") != type_:
                return False
            return all(e.get("data", {}).get(k) == v for k, v in match.items())

        return await self.wait_for(pred, timeout)


async def connect_ws(uri: str):
    import websockets

    return await websockets.connect(uri, max_size=2**20)


async def close_code_of(uri: str) -> int | None:
    """Connect, then read until the server closes; return its code."""
    import websockets
    from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

    try:
        async with await connect_ws(uri) as ws:
            await ws.recv()
            return None  # stayed open — not the rejection we expected
    except (ConnectionClosedError, ConnectionClosedOK) as exc:
        return exc.code


def browser_flow(
    token8: str,
    restaurant_id: str,
    waiter_h: dict,
    kitchen_h: dict,
    uvicorn_proc,
    owner_h: dict | None = None,
    naan_id: str | None = None,
    db_url: str | None = None,
) -> None:
    """Playwright browser flow — runs in a worker thread because the
    sync Playwright API cannot run inside the asyncio event loop.

    Guest (mobile context) drives menu -> cart -> review -> place
    order -> live tracking -> help -> bill -> receipt while the
    kitchen and staff contexts (separate browser contexts, logged in
    as the kitchen user / waiter) watch the same order live.
    Screenshots land in docs/stage9-screenshots/. Then the Stage 9
    additions: the guest session-expiry UI (token revoked -> the
    page settles on "Table not verified yet") and the owner flows
    (overview with real numbers, tax settings, menu price change).
    Finally uvicorn is killed and the guest UI must show "Not
    connected to the backend yet" — never a false success state
    (local mode only; skipped when uvicorn_proc is None).
    """
    import httpx

    if not CHROME.exists():
        raise AssertionError(f"Chromium not found at {CHROME}")
    from playwright.sync_api import sync_playwright

    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    http = httpx.Client(base_url=BASE, timeout=10.0, trust_env=False)

    def shot(page, name: str) -> None:
        page.screenshot(path=str(SHOTS_DIR / name))
        print(f"  shot {name}")

    def current_step(page, label: str) -> None:
        # The tracking timeline marks the live step with a "Current"
        # pill next to its label; the other steps list the same
        # words, so the pill is the assertion.
        page.locator("p", has_text=label).get_by_text("Current").wait_for(
            timeout=15000)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME))
            # --- guest (mobile) ---
            guest_ctx = browser.new_context(viewport={"width": 390, "height": 844})
            gpage = guest_ctx.new_page()
            gpage.goto(f"{VITE}/t/{token8}")
            gpage.wait_for_selector("text=Tandoori Roti", timeout=15000)
            check("guest menu renders in the browser", True)
            shot(gpage, "01-guest-menu.png")
            gpage.locator("article", has_text="Tandoori Roti") \
                .get_by_role("button", name="Add").click()
            gpage.get_by_role("button", name=re.compile(r"Add to cart")).click()
            gpage.get_by_role("link", name="View cart").click()
            gpage.wait_for_selector("text=Tandoori Roti", timeout=10000)
            gpage.get_by_role("link", name=re.compile(r"^Review order")).click()
            gpage.get_by_role("button", name=re.compile(r"^Place order")).click()
            gpage.wait_for_url(f"**/t/{token8}/confirmation", timeout=15000)
            orders = http.get("/api/v1/guest/orders",
                              headers={"X-Table-Token": token8}).json()["data"]
            assert orders, "no orders for table 8"
            ui_order = orders[0]
            ui_order_id, ui_order_number = ui_order["id"], ui_order["orderNumber"]
            check("guest places order in the UI (201 via browser)",
                  True, ui_order_number)
            gpage.goto(f"{VITE}/t/{token8}/orders/{ui_order_id}")
            current_step(gpage, "Placed")
            shot(gpage, "02-order-tracking-placed.png")
            check("tracking page shows Placed live", True)

            # --- kitchen (desktop, logged in as the kitchen user) ---
            kitchen_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            kpage = kitchen_ctx.new_page()
            kpage.goto(f"{VITE}/login")
            kpage.get_by_label("Email").fill(KITCHEN_EMAIL)
            kpage.get_by_label("Password").fill(KITCHEN_PASSWORD)
            kpage.get_by_role("button", name="Sign in").click()
            kpage.wait_for_url("**/kitchen", timeout=15000)
            kpage.wait_for_selector(f"text={ui_order_number}", timeout=15000)
            check("kitchen page shows the new order live", True)
            shot(kpage, "09-kitchen.png")

            # Kitchen moves it; the guest UI follows live, no reload.
            r = http.patch(f"/api/v1/kitchen/orders/{ui_order_id}/status",
                           json={"status": "preparing"}, headers=kitchen_h)
            assert r.status_code == 200, r.text
            current_step(gpage, "Cooking")
            shot(gpage, "03-order-tracking-preparing.png")
            check("guest UI shows Cooking live after kitchen update", True)
            r = http.patch(f"/api/v1/kitchen/orders/{ui_order_id}/status",
                           json={"status": "ready"}, headers=kitchen_h)
            assert r.status_code == 200, r.text
            current_step(gpage, "Ready")
            check("guest UI shows Ready live", True)
            r = http.patch(f"/api/v1/staff/orders/{ui_order_id}/serve",
                           headers=waiter_h)
            assert r.status_code == 200, r.text
            current_step(gpage, "Served")
            shot(gpage, "04-order-tracking-served.png")
            check("guest UI shows Served live after waiter serves", True)

            # --- help, live ---
            gpage.goto(f"{VITE}/t/{token8}/help")
            gpage.wait_for_selector("text=Need a hand?", timeout=15000)
            gpage.get_by_role("button", name="Send request — Request a waiter").click()
            gpage.wait_for_selector("text=Help is on the way", timeout=15000)
            shot(gpage, "05-help-open.png")
            check("help request sent from the UI, status shows live", True)
            calls = http.get("/api/v1/guest/help-calls",
                             headers={"X-Table-Token": token8}).json()["data"]
            ui_call = next(c for c in calls if c["status"] == "open")
            r = http.patch(f"/api/v1/staff/help-calls/{ui_call['id']}/acknowledge",
                           headers=waiter_h)
            assert r.status_code == 200, r.text
            gpage.wait_for_selector("text=A waiter is on the way", timeout=15000)
            shot(gpage, "06-help-acknowledged.png")
            check("guest UI shows acknowledgement live", True)
            r = http.patch(f"/api/v1/staff/help-calls/{ui_call['id']}/resolve",
                           headers=waiter_h)
            assert r.status_code == 200, r.text
            gpage.wait_for_selector("text=was resolved", timeout=15000)
            check("guest UI shows resolved live", True)

            # --- staff (desktop, logged in as the waiter) ---
            staff_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            spage = staff_ctx.new_page()
            spage.goto(f"{VITE}/login")
            spage.get_by_label("Email").fill(WAITER_EMAIL)
            spage.get_by_label("Password").fill(WAITER_PASSWORD)
            spage.get_by_role("button", name="Sign in").click()
            spage.wait_for_url("**/staff", timeout=15000)
            check("staff page loads for the waiter", True)

            # --- bill, live ---
            gpage.goto(f"{VITE}/t/{token8}/bill")
            gpage.wait_for_selector("text=Your bill", timeout=15000)
            gpage.get_by_role("button", name="Request bill").click()
            gpage.wait_for_selector("text=Pay by Cash", timeout=15000)
            shot(gpage, "07-bill.png")
            ui_bill = http.post("/api/v1/guest/bill",
                                headers={"X-Table-Token": token8}).json()["data"]
            check("guest bill requested from the UI", True, ui_bill["billNumber"])
            spage.wait_for_selector(f"text={ui_bill['billNumber']}", timeout=15000)
            shot(spage, "10-staff-billing.png")
            check("staff page shows the requested bill live", True, ui_bill["billNumber"])
            r = http.post(
                f"/api/v1/staff/bills/{ui_bill['id']}/payments"
                f"?restaurant_id={restaurant_id}",
                json={"amountPaise": ui_bill["totalPaise"]},
                headers={**waiter_h, "Idempotency-Key": f"ui-pay-{uuid.uuid4().hex}"},
            )
            assert r.status_code == 201, r.text
            ui_receipt = r.json()["data"]["receipt"]["receiptNumber"]
            gpage.wait_for_selector(f"text={ui_receipt}", timeout=15000)
            shot(gpage, "08-bill-paid-receipt.png")
            check("guest UI shows the receipt live after cash collection",
                  True, ui_receipt)

            print("== 9. guest session-expiry UI (Stage 9, H) ==")
            # Revoke table 8's token (minting a new one revokes the
            # old) and reload the guest page: it must settle on
            # "This table session has expired" with no menu — not
            # loop, not show the old order/receipt as if the
            # session lived.
            if db_url is None:
                print("  SKIP session-expiry UI (no DB access in external mode)")
            else:
                rotate_path = Path(tempfile.mkdtemp(prefix="tavessa-stage9-rotate-", dir=os.path.expanduser("~/.pg-tmp"))) / "rotate.py"
                rotate_path.write_text(ROTATE_SNIPPET)
                run([sys.executable, str(rotate_path), "8"], BACKEND_DIR,
                    {**os.environ, "DATABASE_URL": db_url, "PYTHONPATH": str(BACKEND_DIR)})
                gpage.goto(f"{VITE}/t/{token8}")
                gpage.wait_for_selector("text=This table session has expired", timeout=15000)
                body = gpage.locator("body").inner_text()
                check("revoked token shows 'This table session has expired'",
                      "This table session has expired" in body)
                check("revoked token shows no menu",
                      "Tandoori Roti" not in body)
                import time as _time
                _time.sleep(2)
                body = gpage.locator("body").inner_text()
                check("expired session settles (no loop back to the menu)",
                      "This table session has expired" in body
                      and "Tandoori Roti" not in body)
                shot(gpage, "15-guest-session-expired.png")

            print("== 10. owner flows (Stage 9, F) ==")
            # Owner logs in -> /admin overview with real numbers ->
            # tax settings view -> menu price change visible.
            opage_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            opage = opage_ctx.new_page()
            opage.goto(f"{VITE}/login")
            opage.get_by_label("Email").fill(OWNER_EMAIL)
            opage.get_by_label("Password").fill(OWNER_PASSWORD)
            opage.get_by_role("button", name="Sign in").click()
            opage.wait_for_url("**/admin", timeout=15000)
            opage.wait_for_selector("text=Gross sales", timeout=15000)
            opage.wait_for_selector("text=Cash collected", timeout=15000)
            check("owner overview loads with real numbers", True)
            shot(opage, "12-owner-overview.png")
            # NOTE: the access token lives in memory only (Stage 7
            # design — never localStorage), and there is no
            # boot-time session restore, so a full-page reload
            # drops the owner to /login even with a valid refresh
            # cookie. Owner navigation here therefore uses the
            # sidebar links (client-side, no reload) — the normal
            # in-app path. Recorded as a Stage 9 finding; see the
            # final report.
            opage.get_by_role("link", name="Settings").click()
            # Tax is 10% exclusive GST after section B.
            opage.wait_for_selector("text=Tax configuration", timeout=15000)
            opage.wait_for_selector("text=GST 10% (exclusive)", timeout=15000)
            check("owner tax settings show the configured GST", True)
            shot(opage, "13-owner-tax.png")
            if naan_id is not None and owner_h is not None:
                r = http.patch(
                    f"/api/v1/admin/menu/items/{naan_id}",
                    json={"pricePaise": 5900}, headers=owner_h)
                assert r.status_code == 200, r.text
                opage.get_by_role("link", name="Menu").click()
                opage.wait_for_selector("text=₹59.00", timeout=15000)
                check("menu price change (₹49 -> ₹59) shows on /admin/menu", True)
                shot(opage, "14-owner-menu-price.png")
            else:
                print("  SKIP menu price change (no owner auth in external mode)")

            print("== 8. backend unavailable: no false success ==")
            if uvicorn_proc is None:
                print("  SKIP backend-down section (external mode: must not kill the stack)")
                browser.close()
                return
            uvicorn_proc.terminate()
            try:
                uvicorn_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                uvicorn_proc.kill()
            gpage.goto(f"{VITE}/t/{token8}")
            # The menu page has its own honest error states (it does
            # not use the generic BackendNotConnected box): the
            # session is "not verified yet" and the menu "couldn't
            # load" with the proxy's 502. Either proves the page
            # reports the outage instead of showing stale success.
            try:
                gpage.wait_for_selector("text=Table not verified yet",
                                        timeout=60000)
            except Exception:
                # Diagnostic, not a pass: show what the page actually
                # rendered while the backend was down.
                print("  body while backend down:",
                      gpage.locator("body").inner_text()[:600].replace("\n", " | "))
                gpage.screenshot(path=str(SHOTS_DIR / "11-backend-down.png"))
                raise
            body = gpage.locator("body").inner_text()
            check("UI reports the outage ('Table not verified yet')",
                  "Table not verified yet" in body)
            check("UI reports the menu could not load",
                  "Couldn't load the menu" in body)
            check("no false success state while the backend is down",
                  "Served at your table" not in body
                  and "Receipt" not in body
                  and "Paid" not in body,
                  "page still shows a success state")
            shot(gpage, "11-backend-down.png")
            browser.close()
    finally:
        http.close()


async def main() -> int:
    os.makedirs(os.path.expanduser("~/.pg-tmp"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tavessa-stage9-e2e-", dir=os.path.expanduser("~/.pg-tmp"))
    server = None
    url = None
    env = {**os.environ, "SECRET_KEY": "test-only-secret",
           "ENVIRONMENT": "development"}
    uvicorn = vite = None
    try:
        if EXTERNAL:
            # Compose mode: the stack is already up (see
            # run_e2e_compose.sh); seed output comes from the seed
            # step the wrapper ran inside the api container.
            seed_path = os.environ.get("E2E_SEED_JSON")
            assert seed_path, "E2E_SEED_JSON is required with --external"
            seed = json.loads(Path(seed_path).read_text())
            restaurant_id = seed["restaurant_id"]
            token5, token6, token7, token8 = (
                seed["tokens"][n] for n in ("5", "6", "7", "8")
            )
            check("seeded Zaika + 4 table tokens (external)",
                  bool(restaurant_id and token5 and token8))
            assert wait_http(f"{BASE}/api/v1/health"), "backend did not answer"
            check("backend answers /api/v1/health", True)
            assert wait_http(f"{VITE}/", timeout=60), "web did not answer"
            check("vite dev server answers", True)
        else:
            server = pgserver.get_server(os.path.join(tmp, "pg"))
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
            run([sys.executable, str(BACKEND_DIR / "scripts" / "seed_e2e.py"),
                 str(seed_path)], BACKEND_DIR,
                {**env, "PYTHONPATH": str(BACKEND_DIR)})
            seed = json.loads(seed_path.read_text())
            restaurant_id = seed["restaurant_id"]
            token5, token6, token7, token8 = (
                seed["tokens"][n] for n in ("5", "6", "7", "8")
            )
            check("seeded Zaika + 4 table tokens", bool(restaurant_id and token5 and token8))

            print("== 2. start uvicorn + vite ==")
            uvicorn_log = open(Path(tmp) / "uvicorn.log", "w")
            uvicorn = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app",
                 "--host", "127.0.0.1", "--port", str(PORT)],
                cwd=BACKEND_DIR, env=env, stdout=uvicorn_log, stderr=subprocess.STDOUT,
            )
            assert wait_http(f"{BASE}/api/v1/health"), "backend did not start"
            check("backend answers /api/v1/health", True)
            vite_log = open(Path(tmp) / "vite.log", "w")
            # The kitchen/staff pages are "configured" in DEV only when
            # the dev keys are set (isKitchenConfigured /
            # isStaffConfigured gate on VITE_*_DEV_KEY); without them the
            # queues never load. The values are the backend defaults.
            vite_env = {
                **os.environ,
                "VITE_KITCHEN_DEV_KEY": "dev-kitchen-only",
                "VITE_STAFF_DEV_KEY": "dev-staff-only",
            }
            vite = subprocess.Popen(
                ["node", "node_modules/.bin/vite", "--port", "5173",
                 "--strictPort", "--host", "127.0.0.1"],
                cwd=FRONTEND_DIR, env=vite_env, stdout=vite_log, stderr=subprocess.STDOUT,
            )
            assert wait_http(f"{VITE}/", timeout=60), "vite did not start"
            check("vite dev server answers", True)

        import httpx

        api = httpx.AsyncClient(base_url=BASE, timeout=10.0, trust_env=False)

        def auth(token: str) -> dict:
            return {"Authorization": f"Bearer {token}"}

        async def login(email: str, password: str) -> str:
            r = await api.post("/api/v1/auth/login",
                               json={"email": email, "password": password})
            assert r.status_code == 200, r.text
            return r.json()["data"]["accessToken"]

        print("== 3. users + 5% exclusive tax ==")
        if EXTERNAL:
            # The Compose wrapper bootstraps the owner inside the
            # api container (run_e2e_compose.sh); here we only log in.
            print("  (external: owner bootstrapped by the compose wrapper)")
        else:
            run([sys.executable, "-m", "app.auth.bootstrap",
                 "--restaurant-slug", "zaika", "--email", OWNER_EMAIL],
                BACKEND_DIR, env, input_text=f"{OWNER_PASSWORD}\n{OWNER_PASSWORD}\n")
        owner_token = await login(OWNER_EMAIL, OWNER_PASSWORD)
        owner_h = auth(owner_token)
        for email, password, role in (
            (WAITER_EMAIL, WAITER_PASSWORD, "waiter"),
            (KITCHEN_EMAIL, KITCHEN_PASSWORD, "kitchen"),
        ):
            r = await api.post("/api/v1/admin/users",
                               json={"email": email, "password": password, "role": role},
                               headers=owner_h)
            assert r.status_code == 201, r.text
        # New users are forced through change-password; clear that so
        # the browser login lands straight on their dashboard.
        for email, password in ((WAITER_EMAIL, WAITER_PASSWORD),
                                (KITCHEN_EMAIL, KITCHEN_PASSWORD)):
            tok = await login(email, password)
            r = await api.post("/api/v1/auth/change-password",
                               json={"currentPassword": password,
                                     "newPassword": password},
                               headers=auth(tok))
            assert r.status_code == 200, r.text
        waiter_token = await login(WAITER_EMAIL, WAITER_PASSWORD)
        kitchen_token = await login(KITCHEN_EMAIL, KITCHEN_PASSWORD)
        waiter_h, kitchen_h = auth(waiter_token), auth(kitchen_token)
        check("owner + waiter + kitchen log in", bool(waiter_token and kitchen_token))
        r = await api.post("/api/v1/admin/settings/tax",
                           json={"enabled": True, "rateBps": 500, "mode": "exclusive",
                                 "label": "GST",
                                 "effectiveFrom": datetime.now(timezone.utc).isoformat()},
                           headers=owner_h)
        assert r.status_code == 201, r.text
        check("5% exclusive GST configured",
              r.json()["current"]["rateBps"] == 500, r.text[:200])

        guest5_h = {"X-Table-Token": token5}
        guest6_h = {"X-Table-Token": token6}
        guest7_h = {"X-Table-Token": token7}

        menu = (await api.get("/api/v1/guest/menu", headers=guest5_h)).json()
        naan = next(i for i in menu["items"] if i["name"] == "Butter Naan")
        check("menu lists Butter Naan", bool(naan))

        async def guest_ticket(token: str) -> str:
            r = await api.post("/api/v1/guest/ws/tickets",
                               headers={"X-Table-Token": token})
            assert r.status_code == 201, r.text
            return r.json()["data"]["ticket"]

        async def staff_ticket(channel: str, headers: dict) -> str:
            r = await api.post("/api/v1/ws/tickets", json={"channel": channel},
                               headers=headers)
            assert r.status_code == 200, r.text
            return r.json()["data"]["ticket"]

        print("== 4. golden path: guest/kitchen/staff sockets ==")
        guest5_ws = await connect_ws(
            f"{WS_BASE}/ws/guest?ticket={await guest_ticket(token5)}")
        kitchen_ws = await connect_ws(
            f"{WS_BASE}/ws/kitchen?restaurant_id={restaurant_id}"
            f"&ticket={await staff_ticket('kitchen', kitchen_h)}")
        staff_ws = await connect_ws(
            f"{WS_BASE}/ws/staff?restaurant_id={restaurant_id}"
            f"&ticket={await staff_ticket('staff', waiter_h)}")
        guest7_ws = await connect_ws(
            f"{WS_BASE}/ws/guest?ticket={await guest_ticket(token7)}")
        c_guest, c_kitchen, c_staff, c_other = (
            Collector("guest5"), Collector("kitchen"),
            Collector("staff"), Collector("guest7"),
        )
        tasks = [asyncio.create_task(c.run(ws)) for c, ws in (
            (c_guest, guest5_ws), (c_kitchen, kitchen_ws),
            (c_staff, staff_ws), (c_other, guest7_ws),
        )]

        # Step 1: guest opens the QR menu.
        r = await api.get(f"/api/v1/guest/t/{token5}")
        check("1. guest opens QR menu (200)", r.status_code == 200, str(r.status_code))
        r = await api.get("/api/v1/guest/menu", headers=guest5_h)
        check("1. menu loads", r.status_code == 200 and len(r.json()["items"]) == 24)

        # Step 2: guest places an order.
        order_key = f"e2e-{uuid.uuid4().hex}"
        r = await api.post("/api/v1/orders",
                           json={"items": [{"menuItemId": naan["id"], "quantity": 2}]},
                           headers={**guest5_h, "Idempotency-Key": order_key})
        assert r.status_code == 201, r.text
        order = r.json()["order"]
        order_id, order_number = order["id"], order["orderNumber"]
        check("2. guest places order (201)", True, order_number)
        ev = await c_guest.wait_type("order.created", order_number=order_number)
        check("2. guest socket gets order.created", ev is not None)
        ev = await c_kitchen.wait_type("order.created", order_number=order_number)
        check("3. kitchen sees it live (order.created)", ev is not None)
        ev = await c_staff.wait_type("order.created", order_number=order_number)
        check("3. staff sees it live (order.created)", ev is not None)

        async def kitchen_transition(status: str) -> None:
            r = await api.patch(f"/api/v1/kitchen/orders/{order_id}/status",
                                json={"status": status}, headers=kitchen_h)
            assert r.status_code == 200, r.text

        # Steps 4-5: kitchen -> preparing, guest + staff see it.
        await kitchen_transition("preparing")
        ev = await c_guest.wait_type("order.status_changed", status="preparing")
        check("4-5. guest sees preparing live", ev is not None)
        ev = await c_staff.wait_type("order.status_changed", status="preparing")
        check("4-5. staff sees preparing live", ev is not None)
        ev = await c_kitchen.wait_type("order.status_changed", status="preparing")
        check("4-5. kitchen socket sees its own transition", ev is not None)

        # Steps 6-7: kitchen -> ready.
        await kitchen_transition("ready")
        ev = await c_guest.wait_type("order.status_changed", status="ready")
        check("6-7. guest sees ready live", ev is not None)
        ev = await c_staff.wait_type("order.status_changed", status="ready")
        check("6-7. staff sees ready live", ev is not None)

        # Step 8: waiter serves; guest sees served.
        r = await api.patch(f"/api/v1/staff/orders/{order_id}/serve", headers=waiter_h)
        assert r.status_code == 200, r.text
        ev = await c_guest.wait_type("order.status_changed", status="served")
        check("8. guest sees served live", ev is not None)
        ev = await c_staff.wait_type("order.status_changed", status="served")
        check("8. staff sees served live", ev is not None)

        # Steps 9-11: guest asks for help; staff + guest see it open.
        r = await api.post("/api/v1/guest/help-calls",
                           json={"type": "waiter", "note": "Extra napkins please"},
                           headers=guest5_h)
        assert r.status_code == 201, r.text
        call = r.json()["data"]
        check("9. guest help request placed (201)", True, call["id"][:8])
        ev = await c_staff.wait_type("help_call.opened", help_call_id=call["id"])
        check("10-11. staff receives help_call.opened", ev is not None)
        ev = await c_guest.wait_type("help_call.opened", help_call_id=call["id"])
        check("10-11. guest sees its own help_call.opened", ev is not None)

        # Steps 12-13: staff acknowledges; guest sees it.
        r = await api.patch(f"/api/v1/staff/help-calls/{call['id']}/acknowledge",
                            headers=waiter_h)
        assert r.status_code == 200, r.text
        ev = await c_guest.wait_type("help_call.acknowledged", help_call_id=call["id"])
        check("12-13. guest sees help acknowledged live", ev is not None)

        # Step 14: staff resolves; guest sees it.
        r = await api.patch(f"/api/v1/staff/help-calls/{call['id']}/resolve",
                            headers=waiter_h)
        assert r.status_code == 200, r.text
        ev = await c_guest.wait_type("help_call.resolved", help_call_id=call["id"])
        check("14. guest sees help resolved live", ev is not None)

        # Steps 15-16: bill request; guest + staff see the bill.
        r = await api.post("/api/v1/guest/bill", headers=guest5_h)
        assert r.status_code == 200, r.text
        bill = r.json()["data"]
        check("15. guest requests bill (200)", bill["status"] == "issued",
              f"{bill['billNumber']} {bill['totalPaise']}")
        ev = await c_guest.wait_type("bill.created", bill_id=bill["id"])
        check("15. guest sees bill.created", ev is not None)
        ev = await c_staff.wait_type("bill.created", bill_id=bill["id"])
        check("16. staff receives bill.created", ev is not None)
        r = await api.patch("/api/v1/guest/bill/request-payment", headers=guest5_h)
        assert r.status_code == 200, r.text
        frozen = r.json()["data"]
        check("16. payment requested (bill frozen)", frozen["status"] == "payment_requested",
              f"total {frozen['totalPaise']}")
        ev = await c_guest.wait_type("bill.updated", status="payment_requested")
        check("16. guest sees bill.updated (payment requested)", ev is not None)
        ev = await c_staff.wait_type("bill.requested", bill_id=bill["id"])
        check("16. staff receives bill.requested", ev is not None)

        # Steps 17-19: waiter collects the cash in full; guest sees
        # payment.recorded then bill.paid, and the outstanding drops
        # to zero.
        pay_key = f"pay-{uuid.uuid4().hex}"
        r = await api.post(
            f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={restaurant_id}",
            json={"amountPaise": frozen["totalPaise"]},
            headers={**waiter_h, "Idempotency-Key": pay_key},
        )
        assert r.status_code == 201, r.text
        receipt = r.json()["data"]["receipt"]
        check("17. waiter confirms cash (201)", True, receipt["receiptNumber"])
        ev = await c_guest.wait_type("payment.recorded", bill_id=bill["id"])
        check("18. guest sees payment.recorded live", ev is not None)
        ev = await c_staff.wait_type("payment.recorded", bill_id=bill["id"])
        check("18. staff sees payment.recorded live", ev is not None)
        ev = await c_guest.wait_type("bill.paid", bill_id=bill["id"])
        check("18. guest sees bill.paid live", ev is not None)
        ev = await c_staff.wait_type("bill.paid", bill_id=bill["id"])
        check("18. staff sees bill.paid live", ev is not None)
        r = await api.get("/api/v1/guest/bill", headers=guest5_h)
        check("19. guest bill is paid, outstanding 0",
              r.json()["data"]["status"] == "paid"
              and r.json()["data"]["outstandingPaise"] == 0,
              str(r.json()["data"]["status"]))

        # Step 20: guest views the receipt.
        r = await api.get("/api/v1/guest/bill/receipt", headers=guest5_h)
        check("20. guest views receipt (200)",
              r.status_code == 200
              and r.json()["data"]["receiptNumber"] == receipt["receiptNumber"],
              str(r.status_code))

        # Step 21: the unrelated table received NOTHING.
        await asyncio.sleep(0.5)
        check("21. unrelated table receives none of these events",
              len(c_other.events) == 0, f"got {len(c_other.events)}: {c_other.events[:2]}")

        print("== 5. split-bill correctness with tax (Table 6) ==")
        r = await api.post("/api/v1/orders",
                           json={"items": [{"menuItemId": naan["id"], "quantity": 2}]},
                           headers={**guest6_h, "Idempotency-Key": f"e2e-{uuid.uuid4().hex}"})
        assert r.status_code == 201, r.text
        r = await api.post("/api/v1/guest/bill", headers=guest6_h)
        assert r.status_code == 200, r.text
        bill6 = r.json()["data"]
        check("split bill issued at 5% tax",
              bill6["totalPaise"] == 10290,  # 9800 + 5% GST (490)
              f"total {bill6['totalPaise']}")
        r = await api.post("/api/v1/guest/bill/splits",
                           json={"mode": "equal", "parts": 2}, headers=guest6_h)
        assert r.status_code == 200, r.text
        splits = r.json()["data"]["splits"]
        check("equal splits created at 5% total",
              sorted(s["amountPaise"] for s in splits) == [5145, 5145],
              str([s["amountPaise"] for s in splits]))
        # The tax rate changes AFTER the splits exist — the freeze
        # must re-derive them to the new frozen total.
        r = await api.post("/api/v1/admin/settings/tax",
                           json={"enabled": True, "rateBps": 1000, "mode": "exclusive",
                                 "label": "GST",
                                 "effectiveFrom": datetime.now(timezone.utc).isoformat()},
                           headers=owner_h)
        assert r.status_code == 201, r.text
        r = await api.post("/api/v1/guest/bill", headers=guest6_h)
        assert r.status_code == 200, r.text
        regen = r.json()["data"]
        check("bill regenerated at 10% tax, splits survive unfrozen",
              regen["totalPaise"] == 10780
              and sorted(s["amountPaise"] for s in regen["splits"]) == [5145, 5145],
              f"total {regen['totalPaise']} splits "
              f"{[s['amountPaise'] for s in regen['splits']]}")
        r = await api.patch("/api/v1/guest/bill/request-payment", headers=guest6_h)
        assert r.status_code == 200, r.text
        frozen6 = r.json()["data"]
        frozen_splits = sorted(s["amountPaise"] for s in frozen6["splits"])
        check("freeze re-derives equal splits to the frozen total",
              frozen6["totalPaise"] == 10780 and frozen_splits == [5390, 5390]
              and sum(frozen_splits) == frozen6["totalPaise"],
              f"total {frozen6['totalPaise']} splits {frozen_splits}")
        r = await api.get("/api/v1/admin/audit-logs",
                          params={"restaurant_id": restaurant_id, "per_page": 100},
                          headers=owner_h)
        assert r.status_code == 200, r.text
        reconciled = [row for row in r.json()["data"]
                      if row["action"] == "bill.splits_reconciled"
                      and row.get("details", {}).get("bill_number") == bill6["billNumber"]]
        check("audit row bill.splits_reconciled written",
              len(reconciled) == 1, f"found {len(reconciled)}")
        # A split payment now works against the RECONCILED amount.
        r = await api.post(
            f"/api/v1/staff/bills/{bill6['id']}/payments?restaurant_id={restaurant_id}",
            json={"amountPaise": 5390, "billSplitId": frozen6["splits"][0]["id"]},
            headers={**waiter_h, "Idempotency-Key": f"pay6-{uuid.uuid4().hex}"},
        )
        check("split payment accepted at reconciled amount (201)",
              r.status_code == 201, f"{r.status_code} {r.text[:160]}")

        print("== 6. WS resilience ==")
        # Disconnect + reconnect with a fresh ticket: the new socket
        # receives later events.
        await guest5_ws.close()
        await asyncio.sleep(0.2)
        guest5_ws = await connect_ws(
            f"{WS_BASE}/ws/guest?ticket={await guest_ticket(token5)}")
        c_guest2 = Collector("guest5-reconnected")
        task2 = asyncio.create_task(c_guest2.run(guest5_ws))
        r = await api.post("/api/v1/orders",
                           json={"items": [{"menuItemId": naan["id"], "quantity": 1}]},
                           headers={**guest5_h, "Idempotency-Key": f"e2e-{uuid.uuid4().hex}"})
        assert r.status_code == 201, r.text
        order2 = r.json()["order"]
        order2_number = order2["orderNumber"]
        ev = await c_guest2.wait_type("order.created", order_number=order2_number)
        check("guest reconnect receives later events", ev is not None)
        # Expired ticket -> 4401, then a fresh ticket connects.
        # (Needs direct DB access — skipped in external mode.)
        if url is None:
            print("  SKIP expired-ticket SQL sub-check (no DB access in external mode)")
        else:
            stale = await guest_ticket(token5)
            from sqlalchemy import create_engine, text

            engine = create_engine(url, poolclass=None)
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE ws_tickets SET expires_at = now() - interval '1 second' "
                         "WHERE ticket_hash = :h"),
                    {"h": hashlib.sha256(stale.encode()).hexdigest()},
                )
                conn.commit()
            code = await close_code_of(f"{WS_BASE}/ws/guest?ticket={stale}")
            check("expired ticket rejected (4401)", code == 4401, f"close code {code}")
            fresh_ws = await connect_ws(
                f"{WS_BASE}/ws/guest?ticket={await guest_ticket(token5)}")
            check("fresh ticket connects after expiry", True)
            await fresh_ws.close()
        # Invalid ticket -> 4401.
        code = await close_code_of(f"{WS_BASE}/ws/guest?ticket=not-a-real-ticket")
        check("invalid ticket rejected (4401)", code == 4401, f"close code {code}")
        # Guest ticket cannot open the kitchen channel -> 4403.
        code = await close_code_of(
            f"{WS_BASE}/ws/kitchen?restaurant_id={restaurant_id}"
            f"&ticket={await guest_ticket(token5)}")
        check("guest ticket to /ws/kitchen rejected (4403)", code == 4403,
              f"close code {code}")
        # Duplicate order replay: no second order.created anywhere.
        created_before = {
            "guest": len(c_guest.of_type("order.created")),
            "kitchen": len(c_kitchen.of_type("order.created")),
            "staff": len(c_staff.of_type("order.created")),
        }
        r = await api.post("/api/v1/orders",
                           json={"items": [{"menuItemId": naan["id"], "quantity": 2}]},
                           headers={**guest5_h, "Idempotency-Key": order_key})
        check("duplicate order replayed (201, same order)",
              r.status_code == 201
              and r.json()["order"]["orderNumber"] == order_number,
              f"{r.status_code} {r.text[:120]}")
        await asyncio.sleep(0.5)
        created_after = {
            "guest": len(c_guest.of_type("order.created")),
            "kitchen": len(c_kitchen.of_type("order.created")),
            "staff": len(c_staff.of_type("order.created")),
        }
        check("no duplicate order.created on replay",
              created_before == created_after,
              f"{created_before} -> {created_after}")
        # Duplicate payment replay: no second payment.recorded.
        recorded_before = {
            "guest": len(c_guest.of_type("payment.recorded")),
            "staff": len(c_staff.of_type("payment.recorded")),
        }
        r = await api.post(
            f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={restaurant_id}",
            json={"amountPaise": frozen["totalPaise"]},
            headers={**waiter_h, "Idempotency-Key": pay_key},
        )
        check("duplicate payment replayed (201, same payment)",
              r.status_code == 201, f"{r.status_code} {r.text[:120]}")
        await asyncio.sleep(0.5)
        recorded_after = {
            "guest": len(c_guest.of_type("payment.recorded")),
            "staff": len(c_staff.of_type("payment.recorded")),
        }
        check("no duplicate payment.recorded on replay",
              recorded_before == recorded_after,
              f"{recorded_before} -> {recorded_after}")
        # Isolation held for the whole WS section, not just the
        # golden path: the unrelated table still received nothing.
        await asyncio.sleep(0.5)
        check("unrelated table still receives nothing after all WS steps",
              len(c_other.events) == 0, f"got {len(c_other.events)}: {c_other.events[:2]}")

        print("== 6b. KDS offline/reconnect (Stage 9, G) ==")
        # Kill the kitchen socket mid-service, reconnect with a
        # FRESH staff ticket, re-fetch the queue from REST: the
        # still-active order (order2, placed in section 6) is there
        # exactly once (no loss, no duplicates), and a later order
        # arrives exactly once on the new socket. (The golden-path
        # order is served by now, so it has left the KDS queue —
        # the queue only lists active orders.)
        kitchen_created_before = len(c_kitchen.of_type("order.created"))
        await kitchen_ws.close()
        await asyncio.sleep(0.2)
        kitchen_ws = await connect_ws(
            f"{WS_BASE}/ws/kitchen?restaurant_id={restaurant_id}"
            f"&ticket={await staff_ticket('kitchen', kitchen_h)}")
        c_kitchen2 = Collector("kitchen-reconnected")
        task_k2 = asyncio.create_task(c_kitchen2.run(kitchen_ws))
        tasks.append(task_k2)
        r = await api.get(
            f"/api/v1/kitchen/orders?restaurant_id={restaurant_id}",
            headers=kitchen_h)
        assert r.status_code == 200, r.text
        queue_rows = [o for o in r.json()["data"] if o["id"] == order2["id"]]
        check("KDS reconnect: queue re-fetched, order present once",
              len(queue_rows) == 1 and queue_rows[0]["status"] == "placed",
              f"rows={len(queue_rows)}")
        r = await api.post("/api/v1/orders",
                           json={"items": [{"menuItemId": naan["id"], "quantity": 1}]},
                           headers={**guest6_h, "Idempotency-Key": f"e2e-{uuid.uuid4().hex}"})
        assert r.status_code == 201, r.text
        order3_number = r.json()["order"]["orderNumber"]
        ev = await c_kitchen2.wait_type("order.created", order_number=order3_number)
        check("KDS reconnected socket receives the next order", ev is not None)
        await asyncio.sleep(0.5)
        check("KDS reconnected socket got it exactly once",
              len(c_kitchen2.of_type("order.created")) == 1,
              f"got {len(c_kitchen2.of_type('order.created'))}")
        check("old KDS collector got nothing new after the kill",
              len(c_kitchen.of_type("order.created")) == kitchen_created_before,
              f"was {kitchen_created_before}, now "
              f"{len(c_kitchen.of_type('order.created'))}")

        for ws in (guest5_ws, kitchen_ws, staff_ws, guest7_ws):
            try:
                await ws.close()
            except Exception:
                pass
        for t in tasks + [task2]:
            t.cancel()
        await api.aclose()

        print("== 7. browser flow (Playwright) ==")
        # Sync Playwright cannot run inside the asyncio loop;
        # the browser flow runs in a worker thread instead.
        await asyncio.to_thread(
            browser_flow, token8, restaurant_id, waiter_h, kitchen_h, uvicorn,
            owner_h, naan["id"], url,
        )
        uvicorn = None  # killed inside browser_flow (section 8)


        print(f"\nE2E verification: {len(CHECKS)} checks passed.")
        print(f"Screenshots: {sorted(p.name for p in SHOTS_DIR.iterdir())}")
        return 0
    finally:
        for proc in (vite, uvicorn):
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if server is not None:
            try:
                server.cleanup()
            except Exception:
                pass
        # Remove the run's own temp dir (seed helper + logs); the
        # pgserver cluster inside it is already gone via cleanup().
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
