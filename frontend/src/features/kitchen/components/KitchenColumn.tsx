/** One board column (New / Preparing / Ready) with its count. */
import type { KitchenOrder } from "@/shared/api/types";
import { OrderTicket } from "@/features/kitchen/components/OrderTicket";

export function KitchenColumn({
  title,
  orders,
  restaurantId,
  demo = false,
}: {
  title: string;
  orders: KitchenOrder[];
  restaurantId: string;
  demo?: boolean;
}) {
  return (
    <section aria-label={`${title} orders`}>
      <h2 className="flex items-baseline gap-2 text-lg font-semibold tracking-tight">
        {title}
        <span className="rounded-full bg-stone-900 px-2 py-0.5 text-xs font-bold text-white">
          {orders.length}
        </span>
      </h2>
      <div className="mt-3 space-y-3">
        {orders.length === 0 ? (
          <p className="rounded-xl border border-dashed border-stone-300 bg-white/60 p-4 text-sm text-stone-500">
            No {title.toLowerCase()} orders right now.
          </p>
        ) : (
          orders.map((order) => (
            <OrderTicket
              key={order.id}
              order={order}
              restaurantId={restaurantId}
              demo={demo}
            />
          ))
        )}
      </div>
    </section>
  );
}
