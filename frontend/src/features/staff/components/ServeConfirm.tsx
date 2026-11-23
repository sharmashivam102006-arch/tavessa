/** Serve confirmation — the two-step serve flow (Stage 5).
 *
 * Tap "Serve" → the button becomes an inline confirm ("Confirm
 * serve?") with a Cancel option. Confirming calls the API; the
 * order moves ONLY after the server confirms (the parent's
 * mutation onSuccess invalidates the queries). On failure the
 * error shows on the card and the order stays put — nothing is
 * optimistically removed. In demo mode the button is disabled:
 * demo data is never sent to the server.
 */
import { useState } from "react";

import { ApiError } from "@/shared/api/client";
import { useServeOrder } from "@/features/staff/hooks/useStaff";

export function ServeConfirm({
  orderId,
  restaurantId,
  demo = false,
}: {
  orderId: string;
  restaurantId: string;
  demo?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const serve = useServeOrder(restaurantId);

  if (demo) {
    return (
      <p className="mt-4 rounded-lg bg-stone-100 px-4 py-3 text-center text-sm font-semibold text-stone-600">
        Serve — disabled in demo
      </p>
    );
  }

  if (!confirming) {
    return (
      <button
        type="button"
        disabled={serve.isPending}
        onClick={() => setConfirming(true)}
        className="mt-4 w-full rounded-lg bg-brand-700 px-4 py-3 text-base font-semibold text-white transition hover:bg-brand-800 disabled:cursor-wait disabled:opacity-60"
      >
        Serve
      </button>
    );
  }

  return (
    <div className="mt-4">
      {serve.error ? (
        <p
          className="mb-2 rounded-md bg-red-50 px-2.5 py-2 text-sm font-medium text-red-800"
          role="alert"
        >
          {serve.error instanceof ApiError
            ? serve.error.message
            : "Could not serve this order. It has not moved."}
        </p>
      ) : null}
      <p className="rounded-lg bg-amber-50 px-4 py-3 text-center text-sm font-semibold text-amber-900">
        Mark this order served?
      </p>
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          disabled={serve.isPending}
          onClick={() => serve.mutate(orderId)}
          className="flex-1 rounded-lg bg-brand-700 px-4 py-3 text-base font-semibold text-white transition hover:bg-brand-800 disabled:cursor-wait disabled:opacity-60"
        >
          {serve.isPending ? "Serving…" : "Confirm serve"}
        </button>
        <button
          type="button"
          disabled={serve.isPending}
          onClick={() => setConfirming(false)}
          className="rounded-lg border border-stone-300 bg-white px-4 py-3 text-base font-semibold text-stone-700 transition hover:bg-stone-50 disabled:opacity-60"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
