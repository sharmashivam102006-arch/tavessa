/** One staff order card: an active order + its item snapshots.
 *
 * Order number + table number, ticking elapsed time, items with
 * quantities, variant + add-on names, and special instructions
 * HIGHLIGHTED (amber) — the same ticket content as the KDS, plus
 * the kitchen status. A READY order gets the two-step ServeConfirm;
 * any other active order shows its kitchen status and no serve
 * action (serving is ready -> served only).
 */
import type { KitchenOrder } from "@/shared/api/types";
import { ElapsedTime } from "@/features/kitchen/components/ElapsedTime";
import { ServeConfirm } from "@/features/staff/components/ServeConfirm";
import { ORDER_STATUS_LABELS } from "@/shared/api/types";

export function StaffOrderCard({
  order,
  restaurantId,
  demo = false,
}: {
  order: KitchenOrder;
  restaurantId: string;
  demo?: boolean;
}) {
  const isReady = order.status === "ready";

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
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
              isReady
                ? "bg-green-100 text-green-900"
                : "bg-stone-100 text-stone-700"
            }`}
          >
            {ORDER_STATUS_LABELS[order.status]}
          </span>
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

      {isReady ? (
        <ServeConfirm
          orderId={order.id}
          restaurantId={restaurantId}
          demo={demo}
        />
      ) : (
        <p className="mt-4 rounded-lg bg-stone-100 px-4 py-3 text-center text-sm font-semibold text-stone-600">
          {order.status === "placed" || order.status === "accepted"
            ? "In the kitchen queue — serving opens once it is ready"
            : "Cooking — serving opens once it is ready"}
        </p>
      )}
    </article>
  );
}
