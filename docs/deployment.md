# Tavessa deployment guide

> **Stage 10.1 — configuration only.** This stage prepares `vercel.json`,
> `render.yaml`, and this guide. It deploys nothing and creates no cloud
> resources. Everything below that happens in a dashboard is marked
> **(dashboard)**; everything else is already committed in this repo.

## 1. Architecture

```
Browser ──HTTPS──> Vercel (frontend, https://<project>.vercel.app)
                       │  /api/*  ──rewrite──> Render (FastAPI)
                       │  /ws/*   ──rewrite──> Render (FastAPI)
                       │  /*       ──SPA fallback──> /index.html
Render tavessa-api ──private network──> Render PostgreSQL (tavessa-db)
```

- **Frontend:** Vercel, default `*.vercel.app` URL. **No custom domains.**
- **Backend:** Render Web Service `tavessa-api`, native Python (not Docker),
  `https://tavessa-api.onrender.com`, exactly **one instance**.
- **Database:** Render PostgreSQL 16, same region (`oregon`) as the backend.
- The browser talks **only to the Vercel origin**. Vercel reverse-proxies
  `/api/*` and `/ws/*` to Render, so browser requests are same-origin and
  the existing refresh cookie (`HttpOnly`, `Secure` in production,
  `SameSite=Lax`, `Path /api/v1/auth`) works with **zero auth/cookie
  changes**. The backend stays directly reachable at its Render URL too;
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

**Plans (dashboard decision, not a file change).** The Blueprint pins
`plan: free` for both resources ($0):
- Free web service: sleeps after ~15 min idle — the first request after
  a pause is slow. Wake it a minute before any demo or test.
- Free PostgreSQL: **deleted after 30 days** unless upgraded to a paid
  plan (paid plans also add daily backups). For a lasting deployment,
  upgrade the database in the dashboard.
- To upgrade either, change the plan in the dashboard; no repo change
  is needed.

## 3. Create the Vercel project (dashboard)

1. In the Vercel dashboard: **Add New → Project**, import the same
   `tavessa` GitHub repo.
2. Set **Root Directory to `frontend`** (there is no `package.json` at
   the repo root — without this the build fails). Framework preset:
   **Vite** (auto-detected from `frontend/package.json`); build command
   `npm run build` (`tsc -b && vite build`); output directory `dist`.
3. **Environment variables: set none for the API.** Leave `VITE_API_URL`
   **unset/empty** in the Vercel project. The frontend then uses
   relative URLs (`/api/...`, `/ws/...`) that hit the Vercel origin and
   are proxied to Render. Do **not** set `VITE_API_URL` to the Render
   URL — that would make browser requests cross-origin and break the
   refresh cookie. `VITE_API_URL` is baked in at build time, so an
   empty value also means a future domain change needs no rebuild.
4. Deploy. Vercel reads `vercel.json` from the repo root at deploy time.
5. Note the assigned URL, e.g. `https://tavessa-abc123.vercel.app`.

## 4. Environment variables

| Variable | Where | Value / source |
|---|---|---|
| `ENVIRONMENT` | `render.yaml` (`value: production`) | Committed. Production posture: `/docs` off, dev keys refused, Secure cookie. |
| `SECRET_KEY` | Render dashboard prompt (`sync: false`) | Owner-generated, see §2. Never in the repo. The app refuses to boot in production with the shipped dev default. |
| `DATABASE_URL` | `render.yaml` (`fromDatabase: tavessa-db / connectionString`) | Render injects the private-network URL. The app rewrites bare `postgresql://` to `postgresql+psycopg://` itself (`backend/app/db/session.py`). No SSL mode needed on the private network. |
| `PYTHON_VERSION` | `render.yaml` (`value: "3.12"`) | Committed. Pins the runtime the repo requires (`>= 3.12`). |
| `CORS_ORIGINS` | **Render dashboard, after §3** | **Not in `render.yaml`.** Correct value: the exact Vercel origin from step 3.5, e.g. `https://tavessa-abc123.vercel.app`. Same-origin proxied calls never trigger CORS, so the deployment does not depend on this being set first — it only matters for direct browser calls to the Render host. Never use `*` with credentials. |
| `VITE_API_URL` | Vercel project | **Leave unset/empty** (see §3.3). |
| `FRONTEND_URL`, `BACKEND_URL` | — (not set) | Not used by the backend for CORS or routing (`FRONTEND_URL` is only used by the local demo-token script to print QR URLs; `BACKEND_URL` is unused). Omitted deliberately. |
| `TAVESSA_ALLOW_SQLITE`, dev keys, demo passwords | — (never set) | Must never exist in production. |

## 5. How same-origin API proxying works

`vercel.json` (committed) rewrites, in order:

1. `/api/:path*` → `https://tavessa-api.onrender.com/api/:path*`
   (full path and query string preserved)
2. `/ws/guest`, `/ws/kitchen`, `/ws/staff` → the matching Render endpoints
3. `/(.*)` → `/index.html` (SPA fallback — see §6)

Vercel applies the first matching rewrite, so the API/WS rules take
precedence over the fallback. There are no redirects: the browser URL
never changes to the Render hostname.

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

## 6. How WebSocket proxying works — and its verified limitations

The three sockets (`/ws/guest`, `/ws/kitchen`, `/ws/staff`) are proxied
by the same `vercel.json` rewrites. The frontend builds WS URLs from
`VITE_API_URL`, falling back to `window.location.origin` when it is
empty (`frontend/src/features/{guest,kitchen,staff}/lib/*.ts`), so with
`VITE_API_URL` unset the browser connects to
`wss://<project>.vercel.app/ws/<channel>?ticket=...` — same-origin,
ticket in the query string (browsers cannot set headers on a WS
handshake). WS auth uses single-use 60-second tickets, not cookies,
so the cookie question does not apply to the sockets.

**Status: UNVERIFIED — do not claim real-time works until the live test
below passes.**

- Vercel's official documentation never documents WebSocket upgrades
  through rewrites to an external origin: the word "websocket" appears
  nowhere in the `vercel.json` reference (3,774 lines) nor on the
  "Rewrites on Vercel" guide. Vercel's only documented WebSocket
  capability is Vercel Functions with Fluid Compute
  (https://vercel.com/docs/frameworks/backend — "Vercel Functions can
  serve WebSocket connections when Fluid compute is enabled"), which is
  a different mechanism from proxying to an external backend.
- The strongest explicit statement found is from Convex's docs
  (https://docs.convex.dev/production/abuse-protection): "Vercel rewrites
  proxy HTTP requests, not WebSocket connections." Community reports
  conflict (one user: "Rewrites do [capture websocket traffic]";
  another: "rewrites don't seem to be proxying WS protocol").
- **Live test required after deployment:** on the deployed Vercel URL,
  open the guest tracking page and the kitchen board with a live order
  and confirm WS events flow (order appears on the KDS without a
  refresh; guest tracking updates). In devtools, confirm the WS
  handshake to `wss://<project>.vercel.app/ws/...` returns **101**
  and frames flow. If the handshake fails, stop: real-time is **not**
  deployment-ready through the proxy.
- **Fallback if the proxy cannot carry WS (not implemented in this
  stage):** a minimal frontend change — a separate `VITE_WS_URL`
  pointing at `wss://tavessa-api.onrender.com` — so sockets connect
  directly to Render. That works because WS auth is ticket-based, not
  cookie-based; only the REST refresh cookie needed same-origin. This
  fallback needs its own decision and tests; it is recorded here so
  the deployment is not dead-ended, not built.

The SPA fallback (`/(.*)` → `/index.html`) makes direct navigation and
refresh work for `/`, `/login`, `/t/:tableToken`, `/staff`, `/kitchen`,
`/admin`. It cannot intercept `/api/*` or `/ws/*` because those rules
come first, and static assets are served from the filesystem before
rewrites are evaluated.

## 7. Initialise the production database (Render Shell)

Run in the Render dashboard's **Shell** for the `tavessa-api` service
(or any shell with the service's environment), from `backend/`.
**In this exact order — do not skip or reorder:**

1. **Migrations** (creates all tables; current head `c8d3e5f6a7b8`):
   `alembic upgrade head`
   Verify: `alembic current` shows `c8d3e5f6a7b8 (head)`. Never run
   `alembic downgrade` on production — roll back by restoring a
   database backup instead.
2. **Seed Zaika** (demo restaurant, 10 tables, menu, QR tokens;
   idempotent — upserts by natural key, safe to re-run):
   `python -m app.seed`
3. **Bootstrap the owner** (CLI-only, no public registration exists;
   refuses if an owner already exists; password is prompted twice
   via `getpass` and never appears in shell history):
   `python -m app.auth.bootstrap --email <owner email>`
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

- **Does:** commit `vercel.json`, `render.yaml`, and this guide.
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
- **WebSocket:** all three URL builders
  (`guestSocket.ts:buildGuestWsUrl`, `kitchen.ts:buildKitchenWsUrl`,
  `staff.ts:buildStaffWsUrl`) convert `VITE_API_URL` from `http(s)` to
  `ws(s)` when set, and fall back to `window.location.origin` when it
  is empty — so with `VITE_API_URL` unset the sockets also connect
  same-origin through the proxy. No code change needed.
- **Backend:** entrypoint `app.main:app`, routers already mount
  `/api/v1/*` and `/ws/*` (`backend/app/main.py`); the start command
  uses `$PORT` as Render requires; `DATABASE_URL` from Render's
  `connectionString` is normalised by the app itself.
- **CORS:** same-origin proxied calls never trigger CORS. `CORS_ORIGINS`
  is therefore set in the dashboard after the Vercel origin exists
  (§4), not guessed in the Blueprint.

## Known limitations (carried from the Stage 10.0 audit)

- **WebSocket proxying is UNVERIFIED** (see §6) — the single most
  important open item for this deployment.
- **Rate limiting sees the proxy's IP, not the user's.** The limiter
  keys on `request.client.host` (slowapi `get_remote_address`) and the
  repo has no `--proxy-headers` anywhere, so behind the Vercel proxy
  (and Render's own proxy) the per-IP limits are shared across users.
  The start command in `render.yaml` deliberately does **not** add
  `--proxy-headers` (not approved in this stage); adding it is an
  outstanding recommendation for the next stage. Vercel forwards
  `x-forwarded-for`, so the flag would work once added.
- **Free-tier behaviour:** Render web service sleeps after ~15 min
  idle; free PostgreSQL is deleted after 30 days (§2).
- **Health check** returns HTTP 200 even on an unmigrated database
  (§2) — verify migrations with `alembic current`, not with `/health`.
- **No CSP, no HSTS** in the app (deliberate — Vite inline assets;
  HSTS is a reverse-proxy decision). Confirm what the edge serves
  after deployment.
