/** One KDS ticket: an order + its item snapshots.
 *
 * Large order number + table number, ticking elapsed time, items
 * with large quantities, variant + add-on names, and special
 * instructions HIGHLIGHTED (amber). One large action button per
 * ticket — "Start Preparing" (New) / "Mark Ready" (Preparing) —
 * disabled while its mutation is in flight. The ticket moves ONLY
 * after the server confirms (mutation onSuccess invalidates the
 * queue); on error the message shows on the ticket and it stays put.
 * Ready tickets have no action — serving is Stage 5.
 */
import { ApiError } from "@/shared/api/client";
import type { KitchenOrder } from "@/shared/api/types";
import { useTransitionOrder } from "@/features/kitchen/hooks/useKitchen";
import { ElapsedTime } from "@/features/kitchen/components/ElapsedTime";

export function OrderTicket({
  order,
  restaurantId,
  demo = false,
}: {
  order: KitchenOrder;
  restaurantId: string;
  demo?: boolean;
}) {
  const transition = useTransitionOrder(restaurantId);
  const action =
    order.status === "placed" || order.status === "accepted"
      ? { label: "Start Preparing", status: "preparing" as const }
      : order.status === "preparing"
        ? { label: "Mark Ready", status: "ready" as const }
        : null;

  return (
    <article
      className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm"
      aria-label={`Order ${order.orderNumber}, table ${order.tableNumber}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-2xl font-bold tracking-tight">{order.orderNumber}</p>
          <p className="mt-0.5 text-lg font-semibold text-stone-700">
            Table {order.tableNumber}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <ElapsedTime placedAt={order.placedAt} />
          {demo ? (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900">
              DEMO
            </span>
          ) : null}
        </div>
      </div>

      <ul className="mt-3 space-y-2.5 border-t border-stone-100 pt-3">
        {order.items.map((item) => (
          <li key={item.id}>
            <p className="text-base font-semibold leading-snug">
              <span className="mr-2 inline-flex h-7 min-w-7 items-center justify-center rounded-md bg-stone-900 px-1.5 text-base font-bold text-white">
                {item.quantity}×
              </span>
              {item.name}
            </p>
            {item.variantName ? (
              <p className="mt-0.5 pl-9 text-sm text-stone-600">{item.variantName}</p>
            ) : null}
            {item.addOnNames && item.addOnNames.length > 0 ? (
              <p className="mt-0.5 pl-9 text-sm text-stone-600">
                + {item.addOnNames.join(", ")}
              </p>
            ) : null}
            {item.specialInstructions ? (
              <p className="mt-1.5 rounded-md bg-amber-100 px-2.5 py-1.5 text-sm font-medium text-amber-900">
                Note: {item.specialInstructions}
              </p>
            ) : null}
          </li>
        ))}
      </ul>

      {transition.error ? (
        <p className="mt-3 rounded-md bg-red-50 px-2.5 py-2 text-sm font-medium text-red-800" role="alert">
          {transition.error instanceof ApiError
            ? transition.error.message
            : "Could not update this order. It has not moved."}
        </p>
      ) : null}

      {action ? (
        <button
          type="button"
          disabled={transition.isPending}
          onClick={() =>
            transition.mutate({ orderId: order.id, status: action.status })
          }
          className="mt-4 w-full rounded-lg bg-brand-700 px-4 py-3 text-base font-semibold text-white transition hover:bg-brand-800 disabled:cursor-wait disabled:opacity-60"
        >
          {transition.isPending ? "Updating…" : action.label}
        </button>
      ) : (
        <p className="mt-4 rounded-lg bg-green-50 px-4 py-3 text-center text-sm font-semibold text-green-800">
          Ready — awaiting service
        </p>
      )}
    </article>
  );
}
