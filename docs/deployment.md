# Tavessa deployment guide

> **Stage 10.1 — configuration only.** This stage prepares `vercel.json`,
> `render.yaml`, and this guide. It deploys nothing and creates no cloud
> resources. Everything below that happens in a dashboard is marked
> **(dashboard)**; everything else is already committed in this repo.
>
> **Stage 10.2 validation updates (2026-11-26):** WebSocket architecture
> decided as direct-to-Render (§6); database init corrected for the free
> plan — no Render Shell (§7); `PYTHON_VERSION` corrected to `"3.12.3"`
> (§4); paid-plan backup wording corrected (§2).
>
> **Stage 10.3 implementation updates (2026-11-26):** direct-to-Render
> WebSockets implemented — `wsBase` + `VITE_WS_URL` in the three
> WebSocket builders/hooks, `/ws/*` rewrite rules removed from
> `vercel.json`, `autoDeployTrigger: off` in `render.yaml` (§2);
> `--proxy-headers` reviewed and deliberately not added (see "Known
> limitations"). Live WebSocket verification is still pending (§6).
>
> **Stage 10.4 fix (2026-11-26):** `vercel.json` moved from the repo root to
> `frontend/` — Vercel only reads it from the project's Root Directory
> (`frontend/`, next to `package.json`); the repo-root copy was ignored live
> (both rewrites 404'd until the file moved). See §3 step 4.

## 1. Architecture

```
Browser ──HTTPS──> Vercel (frontend, https://<project>.vercel.app)
                       │  /api/*  ──rewrite──> Render (FastAPI)
                       │  /*       ──SPA fallback──> /index.html
Browser ──WSS───> Render (FastAPI) directly, /ws/* (Stage 10.2 decision;
                       ticket auth, no cookies — see §6)
Render tavessa-api ──private network──> Render PostgreSQL (tavessa-db)
```

- **Frontend:** Vercel, default `*.vercel.app` URL. **No custom domains.**
- **Backend:** Render Web Service `tavessa-api`, native Python (not Docker),
  `https://tavessa-api.onrender.com`, exactly **one instance**.
- **Database:** Render PostgreSQL 16, same region (`oregon`) as the backend.
- The browser talks to the **Vercel origin for REST** and **directly to
  Render for WebSockets**. Vercel reverse-proxies `/api/*` to Render, so
  REST requests are same-origin and the existing refresh cookie
  (`HttpOnly`, `Secure` in production, `SameSite=Lax`,
  `Path /api/v1/auth`) works with **zero auth/cookie changes**. WebSockets
  connect straight to `wss://tavessa-api.onrender.com/ws/...` using the
  existing single-use ticket flow (Stage 10.2 decision — Vercel's official
  docs do not establish WebSocket proxying through external rewrites; see
  §6). The `/ws/*` rewrite rules were removed from `vercel.json` in
  Stage 10.3. The backend stays directly reachable at its Render URL too;
  auth still applies to direct calls.

## 2. Create the Render Blueprint (dashboard)

1. In the Render dashboard: **New → Blueprint**, connect the
   `tavessa` GitHub repo, branch `main`. Render detects `render.yaml`
   at the repo root.
2. Render shows the two resources from the file — web service
   `tavessa-api` and database `tavessa-db` — and prompts for the
   `sync: false` variable:
   - **`SECRET_KEY`** — generate it yourself, never commit it:
     `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
     Paste the output into the prompt. It is never stored in the repo.
3. Apply the Blueprint. Render provisions the database, installs
   dependencies (`pip install -r requirements.txt`), and starts the
   service with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
4. Confirm the service is up: `https://tavessa-api.onrender.com/api/v1/health`
   returns `{"status": "ok", "database": "ok", ...}` (after step 5).
   Note: the health check returns HTTP 200 with `"status": "degraded"`
   when the database is unreachable, and 200 even on an unmigrated
   database — it only runs `SELECT 1`. Do not treat "health is 200"
   as "migrations have run".

**Auto-deploy is off.** `render.yaml` sets `autoDeployTrigger: off`
(Stage 10.3, verified against the official Blueprint YAML Reference),
so pushes to `main` do not deploy by themselves. Deploy manually from
the dashboard: the service → **Manual Deploy → Deploy latest commit**.
If this file is ever removed from the Blueprint, the same setting lives
in the dashboard under Settings → Build & Deploy → Auto-Deploy.

**Plans (dashboard decision, not a file change).** The Blueprint pins
`plan: free` for both resources ($0):
- Free web service: sleeps after ~15 min idle — the first request after
  a pause is slow. Wake it a minute before any demo or test.
- Free PostgreSQL: **deleted after 30 days** unless upgraded to a paid
  plan. Paid plans add continuous point-in-time recovery (restore window
  depends on the workspace plan: 3 days on Hobby, 7 days on Pro) plus
  on-demand logical exports retained 7 days. There is no scheduled
  "daily backup" in Render's current docs. For a lasting deployment,
  upgrade the database in the dashboard.
- **Before applying: check the workspace.** Only **one** free Render
  PostgreSQL can exist per workspace, and the 750 free instance-hours
  per month are shared across all free services in the workspace. If
  another project's free database or backend already lives in this
  workspace, the Blueprint apply may fail (database) or the hours may
  run out (services). Confirm in the Render dashboard first.
- To upgrade either, change the plan in the dashboard; no repo change
  is needed.

## 3. Create the Vercel project (dashboard)

1. In the Vercel dashboard: **Add New → Project**, import the same
   `tavessa` GitHub repo.
2. Set **Root Directory to `frontend`** (there is no `package.json` at
   the repo root — without this the build fails). Framework preset:
   **Vite** (auto-detected from `frontend/package.json`); build command
   `npm run build` (`tsc -b && vite build`); output directory `dist`.
3. **Environment variables.** Leave `VITE_API_URL` **unset/empty** in the
   Vercel project. The frontend then uses relative URLs (`/api/...`)
   that hit the Vercel origin and are proxied to Render. Do **not** set
   `VITE_API_URL` to the Render URL — that would make browser requests
   cross-origin and break the refresh cookie. Set
   `VITE_WS_URL=wss://tavessa-api.onrender.com` in the Vercel project
   (Stage 10.3: the frontend reads it in the three WebSocket hooks).
   Both are baked in at build time, so changing either requires a
   Vercel redeploy to take effect; an empty `VITE_API_URL` also means a
   future domain change needs no rebuild. Neither is a secret — both
   are public configuration embedded in the frontend bundle (see
   `frontend/.env.example`).
4. Deploy. Vercel reads `frontend/vercel.json` at deploy time — it must sit
   in the Vercel project's Root Directory (`frontend/`, next to
   `package.json`). A copy at the repo root is ignored: on the first deploy
   (2026-11-26) `vercel.json` was at the repo root and Vercel loaded neither
   rewrite, so `/api/v1/health` and `/login` both returned Vercel's 404 page
   while the homepage (a static file) loaded fine. The file was moved to
   `frontend/` in the Stage 10.4 fix. Do not move it back to the repo root.
5. Note the assigned URL, e.g. `https://tavessa-abc123.vercel.app`.

## 4. Environment variables

| Variable | Where | Value / source |
|---|---|---|
| `ENVIRONMENT` | `render.yaml` (`value: production`) | Committed. Production posture: `/docs` off, dev keys refused, Secure cookie. |
| `SECRET_KEY` | Render dashboard prompt (`sync: false`) | Owner-generated, see §2. Never in the repo. The app refuses to boot in production with the shipped dev default. |
| `DATABASE_URL` | `render.yaml` (`fromDatabase: tavessa-db / connectionString`) | Render injects the private-network URL. The app rewrites bare `postgresql://` to `postgresql+psycopg://` itself (`backend/app/db/session.py`). No SSL mode needed on the private network. |
| `PYTHON_VERSION` | `render.yaml` (`value: "3.12.3"`) | Committed. Pins the runtime the repo requires (`>= 3.12`). Render requires the full `major.minor.patch` version for this variable. |
| `CORS_ORIGINS` | **Render dashboard, after §3** | **Not in `render.yaml`.** Correct value: the exact Vercel origin from step 3.5, e.g. `https://tavessa-abc123.vercel.app`. Same-origin proxied calls never trigger CORS, so the deployment does not depend on this being set first — it only matters for direct browser calls to the Render host. Never use `*` with credentials. |
| `VITE_API_URL` | Vercel project | **Leave unset/empty** (see §3.3). |
| `VITE_WS_URL` | Vercel project | **Set to `wss://tavessa-api.onrender.com`** (see §3.3 and §6). Read by the three WebSocket hooks; when unset, sockets use the same-origin fallback (local dev). |
| `FRONTEND_URL`, `BACKEND_URL` | — (not set) | Not used by the backend for CORS or routing (`FRONTEND_URL` is only used by the local demo-token script to print QR URLs; `BACKEND_URL` is unused). Omitted deliberately. |
| `TAVESSA_ALLOW_SQLITE`, dev keys, demo passwords | — (never set) | Must never exist in production. |

## 5. How same-origin API proxying works

`frontend/vercel.json` (committed) rewrites, in order:

1. `/api/:path*` → `https://tavessa-api.onrender.com/api/:path*`
   (full path and query string preserved)
2. `/(.*)` → `/index.html` (SPA fallback — see §6)

Vercel applies the first matching rewrite, so the API rule takes
precedence over the fallback. There are no redirects: the browser URL
never changes to the Render hostname for REST calls. WebSockets do
not go through Vercel at all — see §6. (The three `/ws/*` rewrite
rules that were here before were removed in Stage 10.3: Vercel's
official docs do not establish WebSocket proxying through external
rewrites.)

**Evidence (VERIFIED FACT, official Vercel docs):**
- https://vercel.com/docs/routing/rewrites — "Rewrites to external origins
  forward requests to these destinations, effectively allowing Vercel to
  function as a reverse proxy or standalone CDN", with the exact example
  `{ "source": "/api/:path*", "destination": "https://api.example.com/:path*" }`
  and "A request to `/api/users` will be forwarded to
  `https://api.example.com/users` without changing the URL in the browser."
- https://vercel.com/docs/project-configuration/vercel-json#rewrites — the
  external-rewrite and SPA-fallback examples used here; "precedence is
  given to the filesystem prior to rewrites being applied", so built
  assets under `/assets/*` are served as files, not rewritten.

Because the calls are same-origin, no CORS preflight happens for API
calls, and the refresh cookie is first-party. The backend still sends
`Cache-Control: no-store` on `/api/*`, so Vercel does not cache API
responses (Vercel honors upstream cache headers on external rewrites).
Vercel forwards `x-real-ip` / `x-forwarded-for` to the origin — see
"Known limitations" for why that matters.

## 6. How WebSockets work — direct to Render (Stage 10.2 decision)

**Decision (Stage 10.2): WebSockets connect directly to Render. REST
stays proxied through Vercel. Do not rely on the Vercel proxy for
real-time.**

- Vercel's official documentation does not establish WebSocket proxying
  through external rewrites: the word "websocket" appears nowhere in
  the `vercel.json` reference (3,774 lines) nor on the "Rewrites on
  Vercel" guide. Vercel's only documented WebSocket capability is
  serving sockets inside a Vercel Function with Fluid Compute
  (https://vercel.com/docs/functions/websockets) — a different
  mechanism from proxying to an external backend, and one that caps
  connections at the Function maximum duration (300 s on Hobby) and
  forbids in-memory state.
- The strongest explicit statement found is from Convex's docs
  (https://docs.convex.dev/production/abuse-protection): "Vercel rewrites
  proxy HTTP requests, not WebSocket connections."
- The three `/ws/*` rewrite rules were removed from `vercel.json` in
  Stage 10.3 — there is no proxy path for sockets any more. Do not
  claim real-time works through the proxy.

**How the decided path works:** the frontend reads `VITE_WS_URL` (set
to `wss://tavessa-api.onrender.com` in the Vercel project) and connects
straight to Render:
`wss://tavessa-api.onrender.com/ws/<channel>?ticket=...`. WS auth uses
single-use 60-second tickets, not cookies, so the cookie question does
not apply to the sockets — no backend, auth, or cookie change is
needed. CORS does not gate WebSocket handshakes in this implementation
(no `Origin` check in the WS path), so no CORS change is needed either.
Ticket issuance stays on same-origin REST.

**Live test required after deployment:** from the deployed Vercel URL,
open the guest tracking page and the kitchen board with a live order
and confirm WS events flow (order appears on the KDS without a
refresh; guest tracking updates). In devtools, confirm the WS
handshake to `wss://tavessa-api.onrender.com/ws/...` returns **101**
and frames flow. If the handshake fails, stop: real-time is **not**
deployment-ready.

The SPA fallback (`/(.*)` → `/index.html`) makes direct navigation and
refresh work for `/`, `/login`, `/t/:tableToken`, `/staff`, `/kitchen`,
`/admin`. It cannot intercept `/api/*` or `/ws/*` because those rules
come first, and static assets are served from the filesystem before
rewrites are evaluated.

## 7. Initialise the production database

**Render Shell is NOT available on the free plan** (Shell and one-off
jobs are paid-plan features), so the commands below cannot be run in
the dashboard on `plan: free`. Use one of these two supported methods,
from `backend/` in a local checkout, **in this exact order — do not
skip or reorder:**

- **Method A (recommended on the free plan): run locally against the
  external database URL.** In the Render dashboard, copy the database's
  **external** connection string and set it as `DATABASE_URL` in your
  local shell. The external URL **must** carry `?sslmode=require`
  (the app has no SSL handling in code; the internal private-network
  URL needs none). You need a local Python environment with the
  backend dependencies installed (`pip install -r requirements.txt`).
- **Method B: temporarily upgrade the web service to a paid plan,**
  use the dashboard Shell, then downgrade. Cleanest operationally
  (runs in the service's own environment), but costs a paid month
  (pro-rated) and needs a dashboard plan change in both directions.

1. **Migrations** (creates all tables; current head `c8d3e5f6a7b8`):
   `alembic upgrade head`
   Verify: `alembic current` shows `c8d3e5f6a7b8 (head)`. Never run
   `alembic downgrade` on production — roll back by restoring a
   database backup instead.
2. **Seed Zaika** (demo restaurant, 10 tables, menu; idempotent by
   natural key — re-running it creates no duplicates, but it
   **overwrites admin-edited menu data** (prices, availability, names)
   for seeded items, so **never re-run it after the owner edits the
   menu**; it creates no QR tokens — generate a demo token separately
   with `python -m app.seed.demo_tokens --table 5`):
   `python -m app.seed`
3. **Bootstrap the owner** (CLI-only, no public registration exists;
   refuses if an owner already exists; password is prompted twice
   via `getpass` and never appears in shell history):
   `python -m app.auth.bootstrap --restaurant-slug zaika --email <owner email>`
4. **Start using the app.** The API boots cleanly against an
   unmigrated/empty database and fails per-request, so do not treat
   "the service is up" as "the database is ready" — run the steps
   above first.

## 8. One-instance WebSocket constraint

`render.yaml` sets `numInstances: 1` and the start command runs a
single uvicorn worker (no `--workers` flag). This is a **hard
requirement, not a preference**: `backend/app/realtime/manager.py`
holds all WebSocket subscriptions and version counters in one
process's memory. A second instance or worker would silently break
live event delivery — some clients would stop receiving updates with
no error. Never scale this service horizontally without first
introducing shared real-time state (out of scope for v1).

If the backend restarts, in-memory subscriptions are lost; clients
reconnect (guest: backoff 1s→30s with jitter, stops after 3 session
failures; kitchen/staff: reconnect indefinitely) and re-sync via REST,
so no orders are lost — but there is a brief live-update gap.

## 9. Refresh-cookie verification procedure

Run on the deployed pair, in a clean Chrome profile with default
cookie settings. **Do not claim login works until this passes:**

1. Log in as a staff/owner account on the Vercel URL. In devtools →
   Application → Cookies, confirm `tavessa_refresh` is set on the
   **Vercel host** with `HttpOnly; Secure; SameSite=Lax;
   Path=/api/v1/auth`.
2. Hard-reload. In the Network tab confirm `POST /api/v1/auth/refresh`
   carries the `Cookie: tavessa_refresh=…` header, returns **200**
   with a new access token, and the UI stays signed in without
   touching `/login`.
3. Wait past the 15-minute access-token expiry (or force it), trigger
   any authenticated API call, and confirm the 401 → silent refresh →
   retry succeeds once with no visible logout.
4. Close and reopen the browser, then reload: re-login is **expected**
   (the cookie has no `Max-Age`, so it is a session cookie — confirm
   as intended or record as a bug).
5. **If step 2 or 3 returns 401: stop.** Do not change the cookie
   (`SameSite=None` or anything else) without an explicit security
   review.

## 10. What this stage does and does not do

- **Does:** commit `frontend/vercel.json`, `render.yaml`, and this guide.
  Nothing else in the repo changes: no auth, cookie, JWT, CSRF,
  database-model, migration, business-logic, API-contract, or UI
  change was needed (verified — see §11).
- **Does not:** create any cloud account, project, service, or
  database; deploy anything; run migrations; seed a database;
  bootstrap an owner; generate production credentials; or claim
  successful production login or WebSocket behaviour. All of those
  happen after this stage, following this guide, with live verification.

## 11. Why no application code changed

- **REST:** `API_BASE = import.meta.env.VITE_API_URL ?? ""`
  (`frontend/src/shared/api/client.ts:42`). With `VITE_API_URL`
  unset, every call uses a relative URL — same-origin through the
  proxy. Dev behaviour (Vite proxy) is untouched.
- **WebSocket (Stage 10.3):** all three URL builders gained an
  optional `wsBase` parameter (precedence: `wsBase` > `apiBase` >
  `pageOrigin`); the three hooks read `VITE_WS_URL` and pass it as
  `wsBase`. With `VITE_WS_URL=wss://tavessa-api.onrender.com` set in
  Vercel, sockets connect directly to Render; with it unset, the
  previous same-origin behaviour is preserved (local dev). The
  `/ws/*` rewrite rules were removed from `vercel.json`.
- **Backend:** entrypoint `app.main:app`, routers already mount
  `/api/v1/*` and `/ws/*` (`backend/app/main.py`); the start command
  uses `$PORT` as Render requires; `DATABASE_URL` from Render's
  `connectionString` is normalised by the app itself.
- **CORS:** same-origin proxied calls never trigger CORS. `CORS_ORIGINS`
  is therefore set in the dashboard after the Vercel origin exists
  (§4), not guessed in the Blueprint.

## Known limitations (carried from the Stage 10.0 audit)

- **WebSocket architecture implemented in Stage 10.3, live verification
  still pending** (see §6): sockets connect directly to Render via
  `VITE_WS_URL`; the `/ws/*` rewrite rules were removed from
  `vercel.json`. Vercel's official docs do not establish external
  WebSocket proxying, and Convex's docs state rewrites proxy HTTP, not
  WebSockets. Do not claim real-time works until the live 101-handshake
  test passes (see §6).
- **Rate limiting sees the proxy's IP, not the user's.** The limiter
  keys on `request.client.host` (slowapi `get_remote_address`, which
  does not read `X-Forwarded-For`), so behind the Vercel proxy (and
  Render's own proxy) the per-IP limits are shared across users.
  Reviewed in Stage 10.3 and deliberately NOT fixed by adding
  `--proxy-headers`: on its own the flag is inert (uvicorn only
  honours `X-Forwarded-For` from peers listed in
  `--forwarded-allow-ips`, which defaults to loopback only — Render's
  proxy is not loopback), and making it work would need
  `--forwarded-allow-ips '*'`, which would let any client connecting
  directly to the public Render host spoof the IP the limiter keys on
  and evade per-IP limits. A trusted-proxy-aware fix needs its own
  security review; the current shared-IP behaviour fails closed
  (over-throttling under load) rather than letting an attacker evade
  the limit. Vercel forwards `x-forwarded-for`, so the flag would
  work once added — that is not a reason to add it without the trust
  configuration.
- **Free-tier behaviour:** Render web service sleeps after ~15 min
  idle; free PostgreSQL is deleted after 30 days (§2).
- **Health check** returns HTTP 200 even on an unmigrated database
  (§2) — verify migrations with `alembic current`, not with `/health`.
- **No CSP, no HSTS** in the app (deliberate — Vite inline assets;
  HSTS is a reverse-proxy decision). Confirm what the edge serves
  after deployment.
