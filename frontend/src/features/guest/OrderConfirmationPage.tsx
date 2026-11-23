import { Link, useLocation, useParams } from "react-router";
import { CheckCircle2, ReceiptText, UtensilsCrossed } from "lucide-react";

import type { Order } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";
import { StatusBadge } from "@/features/guest/components/StatusBadge";
import { EmptyState } from "@/features/guest/components/states";
import { Button } from "@/components/ui/button";

/**
 * Order confirmation — renders ONLY from a real 201 response passed
 * via location state by OrderReviewPage. There is no demo/fake
 * confirmation: arriving here directly (no state) shows the
 * empty state and links to tracking, where real orders appear once
 * the backend exists.
 */
export function OrderConfirmationPage() {
  const { tableToken = "" } = useParams<{ tableToken: string }>();
  const location = useLocation();
  const order = (location.state as { order?: Order } | null)?.order;

  if (!order) {
    return (
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Order confirmation</h1>
        <div className="mt-4">
          <EmptyState
            icon={<ReceiptText className="h-8 w-8 text-stone-400" />}
            title="No confirmed order to show"
            body="A confirmation appears here only after the backend accepts an order (POST /api/v1/orders, Stage 3). Your placed orders will appear under “Track my orders”."
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Button asChild>
                  <Link to={`/t/${tableToken}/orders`}>Track my orders</Link>
                </Button>
                <Button variant="outline" asChild>
                  <Link to={`/t/${tableToken}`}>Back to menu</Link>
                </Button>
              </div>
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div className="text-center">
      <CheckCircle2 className="mx-auto h-14 w-14 text-brand-700" />
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">Order placed!</h1>
      <p className="mt-1 text-sm text-stone-600">
        Order <span className="font-semibold text-stone-900">{order.orderNumber}</span> is
        with the kitchen. <StatusBadge status={order.status} />
      </p>

      <div className="mt-6 rounded-xl border border-stone-200 bg-white p-4 text-left shadow-sm">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
          Your order
        </h2>
        <ul className="mt-2 space-y-2 text-sm">
          {order.items.map((item) => (
            <li key={item.id} className="flex justify-between gap-2">
              <span>
                {item.quantity} × {item.name}
                {item.variantName ? ` · ${item.variantName}` : ""}
                {item.addOnNames?.length ? ` · ${item.addOnNames.join(", ")}` : ""}
              </span>
              <span className="shrink-0 tabular-nums">{formatINR(item.totalPaise)}</span>
            </li>
          ))}
        </ul>
        <dl className="mt-3 space-y-1 border-t border-stone-200 pt-3 text-sm">
          <div className="flex justify-between">
            <dt>Subtotal</dt>
            <dd className="tabular-nums">{formatINR(order.subtotalPaise)}</dd>
          </div>
          <div className="flex justify-between text-stone-500">
            <dt>Taxes</dt>
            <dd>{order.taxPaise ? formatINR(order.taxPaise) : "Not configured yet (D1)"}</dd>
          </div>
          <div className="flex justify-between text-base font-semibold">
            <dt>Total</dt>
            <dd className="tabular-nums">{formatINR(order.totalPaise)}</dd>
          </div>
        </dl>
        <p className="mt-3 text-xs leading-relaxed text-stone-500">
          This total was calculated by the backend from the menu — the
          estimate you saw in the cart was replaced by the server's figures.
        </p>
      </div>

      <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
        <Button asChild>
          <Link to={`/t/${tableToken}/orders/${order.id}`}>
            <UtensilsCrossed className="h-4 w-4" />
            Track this order
          </Link>
        </Button>
        <Button variant="outline" asChild>
          <Link to={`/t/${tableToken}`}>Order more</Link>
        </Button>
      </div>
    </div>
  );
}
