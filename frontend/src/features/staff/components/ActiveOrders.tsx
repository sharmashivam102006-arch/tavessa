/** Active orders — the waiter's work list (Stage 5).
 *
 * "Ready to serve" first (ready orders are what a waiter can act
 * on now), then the rest oldest first. Each order is a
 * StaffOrderCard with its serve action. Empty state when nothing
 * is active.
 */
import type { KitchenOrder } from "@/shared/api/types";
import { StaffOrderCard } from "@/features/staff/components/StaffOrderCard";
import { sortOrdersReadyFirst } from "@/features/staff/lib/staff";

export function ActiveOrders({
  orders,
  restaurantId,
  demo = false,
}: {
  orders: KitchenOrder[];
  restaurantId: string;
  demo?: boolean;
}) {
  const sorted = sortOrdersReadyFirst(orders);
  const ready = sorted.filter((o) => o.status === "ready");
  const rest = sorted.filter((o) => o.status !== "ready");

  if (orders.length === 0) {
    return (
      <section aria-label="Active orders">
        <h2 className="text-lg font-semibold">Active orders</h2>
        <p className="mt-3 rounded-xl border border-stone-200 bg-white p-6 text-sm text-stone-600">
          No active orders — new guest orders will appear here.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Active orders">
      <h2 className="text-lg font-semibold">
        Active orders{" "}
        <span className="text-sm font-medium text-stone-500">
          ({orders.length})
        </span>
      </h2>
      {ready.length > 0 ? (
        <>
          <h3 className="mt-4 text-sm font-semibold uppercase tracking-wide text-green-800">
            Ready to serve ({ready.length})
          </h3>
          <div className="mt-2 grid gap-4 sm:grid-cols-2">
            {ready.map((order) => (
              <StaffOrderCard
                key={order.id}
                order={order}
                restaurantId={restaurantId}
                demo={demo}
              />
            ))}
          </div>
        </>
      ) : null}
      {rest.length > 0 ? (
        <>
          <h3 className="mt-6 text-sm font-semibold uppercase tracking-wide text-stone-600">
            In progress ({rest.length})
          </h3>
          <div className="mt-2 grid gap-4 sm:grid-cols-2">
            {rest.map((order) => (
              <StaffOrderCard
                key={order.id}
                order={order}
                restaurantId={restaurantId}
                demo={demo}
              />
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}
