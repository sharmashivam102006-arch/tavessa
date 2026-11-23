import { Link, Outlet } from "react-router";
import { UtensilsCrossed } from "lucide-react";

import { useHealth } from "@/shared/hooks/useHealth";

/** Shared shell for the Stage 1 brand screen and the role layouts.
 * Role layouts (Guest/Staff/Kitchen/Admin) get their own shells in
 * their stages; this one proves routing + the health check. */
export function AppLayout() {
  const health = useHealth();
  const backendUp = health.data?.status === "ok";
  const dbUp = health.data?.database === "ok";

  return (
    <div className="min-h-screen bg-stone-50 text-stone-900">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
          <Link to="/" className="flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-700 text-white">
              <UtensilsCrossed className="h-5 w-5" />
            </span>
            <span className="text-lg font-semibold tracking-tight">
              Tavessa
            </span>
          </Link>
          <span
            className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ${
              health.isPending
                ? "bg-stone-100 text-stone-600"
                : backendUp && dbUp
                  ? "bg-green-100 text-green-800"
                  : "bg-amber-100 text-amber-800"
            }`}
            title="Backend connection status (no technical details shown beyond this)"
          >
            <span
              className={`h-2 w-2 rounded-full ${
                health.isPending
                  ? "bg-stone-400"
                  : backendUp && dbUp
                    ? "bg-green-600"
                    : "bg-amber-600"
              }`}
            />
            {health.isPending
              ? "Checking backend…"
              : backendUp && dbUp
                ? "Backend connected"
                : backendUp
                  ? "Backend reachable · database unavailable"
                  : "Backend unavailable"}
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-8">
        <Outlet />
      </main>
      <footer className="border-t border-stone-200 py-4 text-center text-xs text-stone-500">
        Tavessa · Every Table. Every Order. In Sync. · Demo restaurant: Zaika
      </footer>
    </div>
  );
}
