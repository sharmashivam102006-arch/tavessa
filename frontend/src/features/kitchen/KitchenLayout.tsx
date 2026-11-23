/** Kitchen shell — deliberately OUTSIDE AppLayout (see router.tsx):
 * the KDS is a full-width wall screen, and AppLayout's max-w-5xl is
 * too narrow. Dark-green header with Tavessa branding, the
 * restaurant name, a live clock, the connection-status pill, dev-key
 * status, and a manual Refresh button. Header state comes from the
 * kitchen UI store, which KitchenPage writes.
 */
import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ChefHat, RefreshCw } from "lucide-react";

import { getKitchenDevKey } from "@/features/kitchen/api/kitchenApi";
import { ConnectionStatus } from "@/features/kitchen/components/ConnectionStatus";
import { useKitchenUiStore } from "@/features/kitchen/stores/kitchenUiStore";

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

export function KitchenLayout() {
  const connection = useKitchenUiStore((s) => s.connection);
  const restaurantName = useKitchenUiStore((s) => s.restaurantName);
  const clock = useClock();
  const queryClient = useQueryClient();
  const devKeyConfigured = getKitchenDevKey() !== undefined;

  return (
    <div className="min-h-screen bg-stone-100 text-stone-900">
      <header className="bg-green-950 text-white">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-white text-green-950">
              <ChefHat className="h-6 w-6" />
            </span>
            <span>
              <span className="block text-lg font-semibold leading-tight tracking-tight">
                Tavessa · Kitchen
              </span>
              <span className="block text-xs text-green-200">
                {restaurantName ?? "Kitchen Display System"}
              </span>
            </span>
          </Link>
          <div className="flex flex-wrap items-center gap-2 sm:gap-3">
            <span className="rounded-full bg-green-900 px-3 py-1 text-xs font-medium tabular-nums text-green-100">
              {clock}
            </span>
            <ConnectionStatus status={connection} />
            <span
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                devKeyConfigured
                  ? "bg-green-900 text-green-100"
                  : "bg-amber-100 text-amber-900"
              }`}
              title={
                devKeyConfigured
                  ? "Kitchen dev key is configured (development access only)"
                  : "VITE_KITCHEN_DEV_KEY is not set — kitchen access is not configured"
              }
            >
              {devKeyConfigured ? "Dev key set" : "Dev key missing"}
            </span>
            <button
              type="button"
              onClick={() => {
                void queryClient.invalidateQueries({
                  queryKey: ["kitchen-orders"],
                });
              }}
              className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-xs font-semibold text-green-950 transition hover:bg-green-100"
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
        Tavessa · Kitchen Display · Development access only until staff auth
        lands — see README
      </footer>
    </div>
  );
}
