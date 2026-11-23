/** Staff shell — deliberately OUTSIDE AppLayout (see router.tsx):
 * the staff board is a full-width operations screen, and
 * AppLayout's max-w-5xl is too narrow. Amber header with Tavessa
 * branding, the restaurant name, a live clock, the
 * connection-status pill, dev-key status, and a manual Refresh
 * button. Header state comes from the staff UI store, which
 * StaffPage writes.
 */
import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ClipboardList, RefreshCw } from "lucide-react";

import { getStaffDevKey } from "@/features/staff/api/staffApi";
import { ConnectionStatus } from "@/features/kitchen/components/ConnectionStatus";
import { useStaffUiStore } from "@/features/staff/stores/staffUiStore";

function useClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now.toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function StaffLayout() {
  const connection = useStaffUiStore((s) => s.connection);
  const restaurantName = useStaffUiStore((s) => s.restaurantName);
  const clock = useClock();
  const queryClient = useQueryClient();
  const devKeyConfigured = getStaffDevKey() !== undefined;

  return (
    <div className="min-h-screen bg-stone-100 text-stone-900">
      <header className="bg-amber-950 text-white">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-white text-amber-950">
              <ClipboardList className="h-6 w-6" />
            </span>
            <span>
              <span className="block text-lg font-semibold leading-tight tracking-tight">
                Tavessa · Staff
              </span>
              <span className="block text-xs text-amber-200">
                {restaurantName ?? "Waiter / Staff Dashboard"}
              </span>
            </span>
          </Link>
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <span className="rounded-full bg-amber-900 px-3 py-1 text-xs font-medium tabular-nums text-amber-100">
              {clock}
            </span>
            <ConnectionStatus status={connection} />
            <span
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                devKeyConfigured
                  ? "bg-amber-900 text-amber-100"
                  : "bg-amber-100 text-amber-900"
              }`}
              title={
                devKeyConfigured
                  ? "Staff dev key is configured (development access only)"
                  : "VITE_STAFF_DEV_KEY is not set — staff access is not configured"
              }
            >
              {devKeyConfigured ? "Dev key set" : "Dev key missing"}
            </span>
            <button
              type="button"
              onClick={() => {
                void queryClient.invalidateQueries({
                  queryKey: ["staff-tables"],
                });
                void queryClient.invalidateQueries({
                  queryKey: ["staff-orders"],
                });
                void queryClient.invalidateQueries({
                  queryKey: ["staff-help-calls"],
                });
              }}
              className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-xs font-semibold text-amber-950 transition hover:bg-amber-100"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </button>
          </div>
        </div>
      </header>
      <main className="px-4 py-6 sm:px-6">
        <Outlet />
      </main>
      <footer className="border-t border-stone-200 py-4 text-center text-xs text-stone-500">
        Tavessa · Staff Dashboard · Development access only until staff
        auth lands — see README. Bills are never marked paid from this
        screen; cash is collected and confirmed by staff in Stage 6.
      </footer>
    </div>
  );
}
