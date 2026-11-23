import { Check, X } from "lucide-react";

import { ORDER_STATUS_LABELS, ORDER_TRACKING_STEPS, type OrderStatus } from "@/shared/api/types";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<OrderStatus, string> = {
  placed: "bg-stone-100 text-stone-700",
  accepted: "bg-blue-100 text-blue-800",
  preparing: "bg-amber-100 text-amber-800",
  ready: "bg-green-100 text-green-800",
  served: "bg-brand-100 text-brand-700",
  completed: "bg-brand-100 text-brand-700",
  cancelled: "bg-red-100 text-red-800",
};

export function StatusBadge({ status }: { status: OrderStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold",
        STATUS_STYLES[status],
      )}
    >
      {ORDER_STATUS_LABELS[status]}
    </span>
  );
}

function stepIndex(status: OrderStatus): number {
  if (status === "cancelled") return -1;
  if (status === "completed") return ORDER_TRACKING_STEPS.length - 1;
  return ORDER_TRACKING_STEPS.indexOf(status);
}

/** Vertical tracking timeline for the five forward steps
 * (Placed → Accepted → Cooking → Ready → Served). A cancelled order
 * renders the timeline up to the point it reached plus a cancelled
 * marker; it never pretends to progress. */
export function OrderStatusTimeline({ status }: { status: OrderStatus }) {
  const current = stepIndex(status);
  return (
    <ol className="relative space-y-0">
      {ORDER_TRACKING_STEPS.map((step, i) => {
        const done = current >= i;
        const active = current === i;
        const isLast = i === ORDER_TRACKING_STEPS.length - 1;
        return (
          <li key={step} className="relative flex gap-3 pb-6 last:pb-0">
            {!isLast && (
              <span
                aria-hidden
                className={cn(
                  "absolute left-[11px] top-6 h-full w-0.5",
                  current > i ? "bg-brand-600" : "bg-stone-200",
                )}
              />
            )}
            <span
              className={cn(
                "relative z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2",
                done
                  ? "border-brand-600 bg-brand-600 text-white"
                  : "border-stone-300 bg-white text-stone-400",
              )}
            >
              {done ? <Check className="h-3.5 w-3.5" /> : <span className="h-1.5 w-1.5 rounded-full bg-stone-300" />}
            </span>
            <div className="pt-0.5">
              <p className={cn("text-sm font-semibold", done ? "text-stone-900" : "text-stone-500")}>
                {ORDER_STATUS_LABELS[step]}
                {active && status !== "completed" ? (
                  <span className="ml-2 rounded-full bg-brand-100 px-2 py-0.5 text-xs font-medium text-brand-700">
                    Current
                  </span>
                ) : null}
              </p>
              <p className="text-xs text-stone-500">
                {step === "placed" && "Your order has reached the kitchen."}
                {step === "accepted" && "The kitchen has accepted your order."}
                {step === "preparing" && "Your food is being cooked."}
                {step === "ready" && "Your food is ready to be served."}
                {step === "served" && "Served at your table. Enjoy!"}
              </p>
            </div>
          </li>
        );
      })}
      {status === "cancelled" ? (
        <li className="relative flex gap-3 pt-2">
          <span className="relative z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 border-red-600 bg-red-600 text-white">
            <X className="h-3.5 w-3.5" />
          </span>
          <div className="pt-0.5">
            <p className="text-sm font-semibold text-red-700">Cancelled</p>
            <p className="text-xs text-stone-500">This order was cancelled. It was not charged.</p>
          </div>
        </li>
      ) : null}
    </ol>
  );
}
