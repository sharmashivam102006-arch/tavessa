import { Link, useParams } from "react-router";
import { ArrowLeft, RefreshCw } from "lucide-react";

import { useGuestOrder } from "@/features/guest/hooks/useGuest";
import { GuestConnectionLine } from "@/features/guest/components/ConnectionIndicator";
import { formatINR } from "@/shared/utils/money";
import { OrderStatusTimeline, StatusBadge } from "@/features/guest/components/StatusBadge";
import { BackendNotConnected, LoadingState } from "@/features/guest/components/states";
import { Button } from "@/components/ui/button";

/** Live order tracking — the status timeline plus the order's items.
 * Status arrives over the guest WebSocket (Stage 8): the kitchen's
 * placed → preparing → ready → served progression and a cancellation
 * invalidate this order's query and the page re-fetches via REST —
 * nothing is shown as successful until the backend confirms it, and
 * no prep-time estimate is ever invented. While the socket is down
 * the page shows the last saved state and the connection line says
 * so; REST refetch on reconnect/focus keeps it honest. */
export function OrderTrackingPage() {
  const { tableToken = "", orderId = "" } = useParams<{ tableToken: string; orderId: string }>();
  const orderQuery = useGuestOrder(tableToken, orderId);

  return (
    <div>
      <Button variant="ghost" size="sm" asChild className="mb-2 -ml-2">
        <Link to={`/t/${tableToken}/orders`}>
          <ArrowLeft className="h-4 w-4" />
          My orders
        </Link>
      </Button>

      {orderQuery.isPending ? (
        <LoadingState label="Loading your order…" />
      ) : orderQuery.error || !orderQuery.data ? (
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Track order</h1>
          <div className="mt-4">
            <BackendNotConnected
              endpoint={`GET /api/v1/guest/orders/${orderId}`}
              stage="Stage 3 (backend APIs & database integration)"
              what="Loading this order's live status"
            >
              <p className="mt-2 text-sm text-amber-800">
                Live tracking updates arrive over the guest WebSocket;
                the REST re-fetch behind this screen is what confirms
                each status. Until the backend answers, no status is
                shown rather than a simulated one.
              </p>
              <Button variant="outline" size="sm" className="mt-3" onClick={() => orderQuery.refetch()}>
                <RefreshCw className="h-4 w-4" />
                Retry
              </Button>
            </BackendNotConnected>
          </div>
        </div>
      ) : (
        (() => {
          const order = orderQuery.data.data;
          return (
            <div>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h1 className="text-2xl font-semibold tracking-tight">
                  Order {order.orderNumber}
                </h1>
                <StatusBadge status={order.status} />
              </div>
              <p className="mt-1 flex items-center gap-2 text-sm text-stone-600">
                <RefreshCw className="h-3.5 w-3.5" />
                Status comes from the kitchen via the backend — live while
                connected, last saved state while reconnecting.
              </p>
              <GuestConnectionLine />

              <div className="mt-6 rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
                <OrderStatusTimeline status={order.status} />
              </div>

              <div className="mt-4 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
                  Items
                </h2>
                <ul className="mt-2 space-y-2 text-sm">
                  {order.items.map((item) => (
                    <li key={item.id} className="flex justify-between gap-2">
                      <span>
                        {item.quantity} × {item.name}
                        <span className="ml-2 text-xs text-stone-500">{item.status}</span>
                      </span>
                      <span className="shrink-0 tabular-nums">{formatINR(item.totalPaise)}</span>
                    </li>
                  ))}
                </ul>
                <div className="mt-3 flex justify-between border-t border-stone-200 pt-3 text-base font-semibold">
                  <span>Total</span>
                  <span className="tabular-nums">{formatINR(order.totalPaise)}</span>
                </div>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <Button variant="outline" asChild>
                  <Link to={`/t/${tableToken}/bill`}>View bill</Link>
                </Button>
                <Button variant="outline" asChild>
                  <Link to={`/t/${tableToken}/help`}>Need help?</Link>
                </Button>
              </div>
            </div>
          );
        })()
      )}
    </div>
  );
}
