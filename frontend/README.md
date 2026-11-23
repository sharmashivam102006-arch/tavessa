# Tavessa frontend

React 19 + TypeScript + Vite, Tailwind CSS v4, shadcn/ui, React Router,
Zustand, TanStack Query, Lucide. See the repo-root `README.md` and
`docs/architecture.md` for the design.

## Setup

```bash
cd frontend
npm install
cp ../.env.example ../.env   # only VITE_API_URL matters here; dev proxy is the default
```

## Run

```bash
npm run dev      # http://localhost:5173 (proxies /api to http://localhost:8000)
npm run build    # tsc + vite build
npm run preview  # serve the production build
```

The home page shows the Tavessa brand and a backend connection status
(green = backend + database ok, amber = degraded/unreachable). No business
features exist yet — guest/staff/kitchen/admin pages are Stage 1 route
shells, built in Stages 2, 5, 4 and 7 respectively.

## Env

| Variable | Purpose |
|---|---|
| `VITE_API_URL` | Backend base URL in production (e.g. `https://api.example.com`). Leave unset in dev — the Vite proxy handles `/api`. |

Money is integer paise everywhere; format it only with
`shared/utils/money.ts` (`formatINR`).
