import { Link, useParams } from "react-router";
import { Clock3, ReceiptText, RefreshCw } from "lucide-react";

import { useGuestOrders } from "@/features/guest/hooks/useGuest";
import { formatINR } from "@/shared/utils/money";
import { StatusBadge } from "@/features/guest/components/StatusBadge";
import { BackendNotConnected, EmptyState, LoadingState } from "@/features/guest/components/states";
import { Button } from "@/components/ui/button";

/** My orders — attempts the real GET /guest/orders. No fallback:
 * orders are never invented. While the backend is missing, the
 * not-connected state is shown. */
export function OrdersPage() {
  const { tableToken = "" } = useParams<{ tableToken: string }>();
  const ordersQuery = useGuestOrders(tableToken);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">My orders</h1>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => ordersQuery.refetch()}
          disabled={ordersQuery.isFetching}
        >
          <RefreshCw className={`h-4 w-4 ${ordersQuery.isFetching ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {ordersQuery.isPending ? (
        <LoadingState label="Loading your orders…" />
      ) : ordersQuery.error || !ordersQuery.data ? (
        <div className="mt-4">
          <BackendNotConnected
            endpoint="GET /api/v1/guest/orders"
            stage="Stage 3 (backend APIs & database integration)"
            what="Loading your orders"
          >
            <p className="mt-2 text-sm text-amber-800">
              No orders have been placed from this table yet in this demo —
              and none are shown here until the backend returns them.
            </p>
          </BackendNotConnected>
        </div>
      ) : ordersQuery.data.data.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            icon={<ReceiptText className="h-8 w-8 text-stone-400" />}
            title="No orders yet"
            body="Orders you place from this table will appear here, with live status updates."
            action={
              <Button asChild>
                <Link to={`/t/${tableToken}`}>Browse the menu</Link>
              </Button>
            }
          />
        </div>
      ) : (
        <ul className="mt-4 space-y-3">
          {ordersQuery.data.data.map((order) => (
            <li key={order.id}>
              <Link
                to={`/t/${tableToken}/orders/${order.id}`}
                className="block rounded-xl border border-stone-200 bg-white p-4 shadow-sm transition-colors hover:border-brand-600"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="font-semibold">Order {order.orderNumber}</p>
                  <StatusBadge status={order.status} />
                </div>
                <p className="mt-1 text-sm text-stone-600">
                  {order.items.length} item{order.items.length === 1 ? "" : "s"} ·{" "}
                  {formatINR(order.totalPaise)}
                </p>
                <p className="mt-0.5 flex items-center gap-1 text-xs text-stone-500">
                  <Clock3 className="h-3.5 w-3.5" />
                  Placed {new Date(order.placedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
