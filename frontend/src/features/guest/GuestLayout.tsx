import { useEffect } from "react";
import { Link, Outlet, useLocation, useParams } from "react-router";
import { Bell, ShoppingBag, UtensilsCrossed } from "lucide-react";

import { useGuestSession, useMenuItemLookup } from "@/features/guest/hooks/useGuest";
import {
  GuestSocketProvider,
  useGuestSocket,
} from "@/features/guest/hooks/useGuestSocket";
import { GuestConnectionPill } from "@/features/guest/components/ConnectionIndicator";
import { useCartStore } from "@/features/guest/stores/cartStore";
import { formatINR } from "@/shared/utils/money";
import { Button } from "@/components/ui/button";

/**
 * Guest shell — deliberately OUTSIDE AppLayout (see router.tsx) so
 * guests never see the dev backend-status pill.
 * Sticky Zaika header (table badge + Help), bottom cart bar.
 * The table number is shown only once the session endpoint verifies
 * it; until then the badge reads "Table — not verified yet".
 */
export function GuestLayout() {
  const { tableToken = "" } = useParams<{ tableToken: string }>();
  const location = useLocation();
  const session = useGuestSession(tableToken);
  const setTableToken = useCartStore((s) => s.setTableToken);
  const itemCount = useCartStore((s) => s.itemCount());
  const findMenuItem = useMenuItemLookup(tableToken);
  const estimatedSubtotal = useCartStore((s) => s.estimatedSubtotal(findMenuItem));
  // Stage 8: the ONE guest socket for this session lives here —
  // the layout wraps every guest page, so it is opened once and
  // shared via GuestSocketProvider. It is disabled for the demo
  // preview (no backend session) and reports offline there.
  const socket = useGuestSocket(tableToken);

  useEffect(() => {
    setTableToken(tableToken);
  }, [tableToken, setTableToken]);

  const isDemo = tableToken === "demo";
  const tableLabel = session.data
    ? `Table ${session.data.table.number}`
    : "Table — not verified yet";
  const showCartBar =
    itemCount > 0 &&
    !location.pathname.endsWith("/cart") &&
    !location.pathname.endsWith("/review");

  return (
    <GuestSocketProvider value={socket}>
    <div className="min-h-screen bg-stone-50 text-stone-900">
      <header className="sticky top-0 z-40 border-b border-stone-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-3">
          <Link to={`/t/${tableToken}`} className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-700 text-white">
              <UtensilsCrossed className="h-5 w-5" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-lg font-semibold leading-tight tracking-tight">
                Zaika
              </span>
              <span className="block truncate text-xs text-stone-500">
                {isDemo ? "Demo preview · " : ""}
                {tableLabel}
              </span>
            </span>
          </Link>
          <div className="flex shrink-0 items-center gap-2">
            {isDemo ? null : <GuestConnectionPill />}
            <Button variant="outline" size="sm" asChild>
              <Link to={`/t/${tableToken}/help`}>
                <Bell className="h-4 w-4" />
                Help
              </Link>
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 pb-28 pt-4 sm:pb-24">
        <Outlet />
      </main>

      {showCartBar ? (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-stone-200 bg-white/95 backdrop-blur">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <p className="text-sm font-semibold">
                {itemCount} item{itemCount === 1 ? "" : "s"} · {formatINR(estimatedSubtotal)}
              </p>
              <p className="truncate text-xs text-stone-500">
                Estimated total — final amount is calculated by the backend
              </p>
            </div>
            <Button asChild className="shrink-0">
              <Link to={`/t/${tableToken}/cart`}>
                <ShoppingBag className="h-4 w-4" />
                View cart
              </Link>
            </Button>
          </div>
        </div>
      ) : null}

      <footer className="pb-6 text-center text-xs text-stone-400">
        Tavessa · Zaika · Cash payments only
      </footer>
    </div>
    </GuestSocketProvider>
  );
}
