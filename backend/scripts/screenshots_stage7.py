"""Stage 7 screenshots — run manually, not part of pytest.

Boots the real backend (uvicorn :8000, real PostgreSQL via pgserver)
and the Vite dev server (:5173), seeds Zaika, creates live data
(owner + waiter + kitchen users, a paid bill with a partial refund,
a voided bill, an unpaid bill, a 5% GST config), logs in as the
owner in headless Chromium, and captures the Stage 7 pages:

  01-login.png, 02-admin-overview.png, 03-menu.png, 04-staff.png,
  05-tax-settings.png, 06-bills.png, 07-bill-void-confirm.png,
  08-cash-report.png, 09-audit-log.png

Usage:  cd backend && .venv/bin/python scripts/screenshots_stage7.py
Screenshots land in docs/stage7-screenshots/. Servers are stopped
afterwards; nothing here touches the developer's real database.
Requires: playwright (pip install playwright) + a Chromium binary
in ~/.cache/ms-playwright (already present).

Demo passwords: read from TAVESSA_DEMO_*_PASSWORD env vars when set,
otherwise generated fresh per run (scripts/demo_passwords.py) — never
hardcoded. See backend/README.md, "Local demo scripts".
"""

from __future__ import annotations

import json
import os
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
SHOTS_DIR = REPO_DIR / "docs" / "stage7-screenshots"
CHROME = Path.home() / ".cache" / "ms-playwright" / "chromium-1255" / "chrome-linux64" / "chrome"

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_passwords import ENV_KITCHEN, ENV_OWNER, ENV_WAITER, demo_password

import pytest

pgserver = pytest.importorskip("pgserver")

OWNER_EMAIL = "owner@zaika.example"
OWNER_PASSWORD = demo_password(ENV_OWNER)
WAITER_EMAIL = "waiter@zaika.example"
WAITER_PASSWORD = demo_password(ENV_WAITER)
KITCHEN_EMAIL = "kitchen@zaika.example"
KITCHEN_PASSWORD = demo_password(ENV_KITCHEN)

SEED_SNIPPET = """\
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
"""


def run(cmd, cwd, env, input_text=None, check=True):
    result = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, input=input_text
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"{' '.join(map(str, cmd))} failed (exit {result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def wait_http(url, timeout=30):
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


def main() -> int:
    os.makedirs(os.path.expanduser("~/.pg-tmp"), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="tavessa-stage7-shots-", dir=os.path.expanduser("~/.pg-tmp"))
    server = pgserver.get_server(os.path.join(tmp, "pg"))
    uvicorn = vite = None
    try:
        uri = server.get_uri()
        url = uri.replace("postgresql://", "postgresql+psycopg://", 1)
        env = {
            **os.environ,
            "DATABASE_URL": url,
            "SECRET_KEY": "test-only-secret",
            "ENVIRONMENT": "development",
        }
        run([sys.executable, "-m", "alembic", "upgrade", "head"], BACKEND_DIR, env)
        seed_path = Path(tmp) / "seed.json"
        seed_snippet = Path(tmp) / "seed_stage7.py"
        seed_snippet.write_text(SEED_SNIPPET)
        run([sys.executable, str(seed_snippet), str(seed_path)], BACKEND_DIR,
            {**env, "PYTHONPATH": str(BACKEND_DIR)})
        seed = json.loads(seed_path.read_text())
        restaurant_id = seed["restaurant_id"]
        token5, token6 = seed["tokens"]["5"], seed["tokens"]["6"]

        uvicorn_log = open(Path(tmp) / "uvicorn.log", "w")
        uvicorn = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", "8000"],
            cwd=BACKEND_DIR, env=env, stdout=uvicorn_log, stderr=subprocess.STDOUT,
        )
        assert wait_http("http://127.0.0.1:8000/api/v1/health"), "backend did not start"

        vite_log = open(Path(tmp) / "vite.log", "w")
        # Launch vite directly (not via `npm run dev`): npm spawns vite
        # as a child, and terminating the npm wrapper would orphan it.
        # --host 127.0.0.1: vite otherwise binds IPv6 localhost only
        # and 127.0.0.1 refuses connections.
        vite = subprocess.Popen(
            ["node", "node_modules/.bin/vite", "--port", "5173",
             "--strictPort", "--host", "127.0.0.1"],
            cwd=FRONTEND_DIR, stdout=vite_log, stderr=subprocess.STDOUT,
        )
        assert wait_http("http://127.0.0.1:5173/", timeout=60), "vite did not start"

        import httpx

        api = httpx.Client(base_url="http://127.0.0.1:8000", timeout=10.0, trust_env=False)
        run([sys.executable, "-m", "app.auth.bootstrap",
             "--restaurant-slug", "zaika", "--email", OWNER_EMAIL],
            BACKEND_DIR, env, input_text=f"{OWNER_PASSWORD}\n{OWNER_PASSWORD}\n")
        owner_token = api.post(
            "/api/v1/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        ).json()["data"]["accessToken"]
        owner_h = {"Authorization": f"Bearer {owner_token}"}
        for email, password, role in (
            (WAITER_EMAIL, WAITER_PASSWORD, "waiter"),
            (KITCHEN_EMAIL, KITCHEN_PASSWORD, "kitchen"),
        ):
            r = api.post("/api/v1/admin/users",
                         json={"email": email, "password": password, "role": role},
                         headers=owner_h)
            assert r.status_code == 201, r.text
        waiter_token = api.post(
            "/api/v1/auth/login",
            json={"email": WAITER_EMAIL, "password": WAITER_PASSWORD},
        ).json()["data"]["accessToken"]

        # Live data: Table 5 order -> bill -> paid in full by waiter,
        # then a partial refund; Table 6 order -> bill -> voided;
        # Table 6 again -> unpaid bill (for the void dialog shot).
        guest_h = {"X-Table-Token": token5}
        menu = api.get("/api/v1/guest/menu", headers=guest_h).json()
        naan = next(i for i in menu["items"] if i["name"] == "Butter Naan")
        paneer = next(i for i in menu["items"] if i["name"] == "Paneer Butter Masala")

        def place(token, items):
            r = api.post("/api/v1/orders", json={"items": items},
                         headers={"X-Table-Token": token,
                                  "Idempotency-Key": f"shot-{uuid.uuid4().hex}"})
            assert r.status_code == 201, r.text

        def bill_for(token):
            r = api.post("/api/v1/guest/bill", headers={"X-Table-Token": token})
            assert r.status_code == 200, r.text
            return r.json()["data"]

        place(token5, [{"menuItemId": naan["id"], "quantity": 2},
                       {"menuItemId": paneer["id"], "quantity": 1}])
        bill = bill_for(token5)
        api.patch("/api/v1/guest/bill/request-payment", headers=guest_h)
        r = api.post(
            f"/api/v1/staff/bills/{bill['id']}/payments?restaurant_id={restaurant_id}",
            json={"amountPaise": bill["totalPaise"]},
            headers={"Authorization": f"Bearer {waiter_token}",
                     "Idempotency-Key": f"pay-{uuid.uuid4().hex}"},
        )
        assert r.status_code == 201, r.text
        payments = api.get("/api/v1/admin/payments",
                           params={"restaurant_id": restaurant_id},
                           headers=owner_h).json()["data"]
        payment = next(p for p in payments if p["billId"] == bill["id"])
        r = api.post(f"/api/v1/admin/bills/{bill['id']}/refunds",
                     json={"paymentId": payment["id"], "amountPaise": 1000,
                           "reason": "Wrong dish"},
                     headers={**owner_h, "Idempotency-Key": f"ref-{uuid.uuid4().hex}"})
        assert r.status_code == 201, r.text

        guest6_h = {"X-Table-Token": token6}
        place(token6, [{"menuItemId": naan["id"], "quantity": 1}])
        bill2 = bill_for(token6)
        r = api.post(f"/api/v1/admin/bills/{bill2['id']}/void",
                     json={"reason": "Guest left"}, headers=owner_h)
        assert r.status_code == 200, r.text
        place(token6, [{"menuItemId": paneer["id"], "quantity": 1}])
        unpaid = bill_for(token6)
        print(f"  live data: paid bill {bill['billNumber']}, voided {bill2['billNumber']}, "
              f"unpaid {unpaid['billNumber']} ({unpaid['totalPaise']} paise)")

        # A 5% GST config so the settings page shows a real config.
        r = api.post("/api/v1/admin/settings/tax",
                     json={"enabled": True, "rateBps": 500, "mode": "exclusive",
                           "label": "GST",
                           "effectiveFrom": datetime.now(timezone.utc).isoformat()},
                     headers=owner_h)
        assert r.status_code == 201, r.text

        # --- Chromium -------------------------------------------------
        from playwright.sync_api import sync_playwright

        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME))
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto("http://127.0.0.1:5173/login")
            page.wait_for_selector("text=Sign in")
            page.screenshot(path=str(SHOTS_DIR / "01-login.png"))
            print("  shot 01-login.png")
            page.get_by_label("Email").fill(OWNER_EMAIL)
            page.get_by_label("Password").fill(OWNER_PASSWORD)
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url("**/admin", timeout=10000)
            page.wait_for_selector("text=Gross sales")
            page.screenshot(path=str(SHOTS_DIR / "02-admin-overview.png"))
            print("  shot 02-admin-overview.png")
            # NOTE: navigate via the sidebar links, NOT page.goto — a
            # full reload wipes the in-memory access token (Stage 7
            # keeps it out of localStorage by design) and bounces to
            # /login.
            def go(link_name, wait_text):
                page.get_by_role("link", name=link_name).click()
                page.wait_for_selector(f"text={wait_text}")

            go("Menu", "Paneer Butter Masala")
            page.screenshot(path=str(SHOTS_DIR / "03-menu.png"))
            print("  shot 03-menu.png")
            go("Staff", WAITER_EMAIL)
            page.screenshot(path=str(SHOTS_DIR / "04-staff.png"))
            print("  shot 04-staff.png")
            go("Settings", "Current:")  # loaded state, not the always-rendered form
            page.screenshot(path=str(SHOTS_DIR / "05-tax-settings.png"))
            print("  shot 05-tax-settings.png")
            go("Bills", unpaid["billNumber"])
            page.screenshot(path=str(SHOTS_DIR / "06-bills.png"))
            print("  shot 06-bills.png")
            # Void confirmation dialog for the unpaid bill.
            rows = page.locator("tr", has_text=unpaid["billNumber"])
            rows.get_by_role("button", name="Void").click()
            page.wait_for_selector("text=Void this bill?")
            page.screenshot(path=str(SHOTS_DIR / "07-bill-void-confirm.png"))
            print("  shot 07-bill-void-confirm.png")
            page.get_by_role("button", name="Cancel").click()
            go("Cash reports", "Gross cash collected")  # report stat cards, not the header
            page.screenshot(path=str(SHOTS_DIR / "08-cash-report.png"))
            print("  shot 08-cash-report.png")
            go("Audit log", "refund.created")
            page.screenshot(path=str(SHOTS_DIR / "09-audit-log.png"))
            print("  shot 09-audit-log.png")
            browser.close()

        print(f"\nScreenshots: {sorted(p.name for p in SHOTS_DIR.iterdir())}")
        return 0
    finally:
        for proc in (vite, uvicorn):
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
