/** Selected-table detail (Stage 5).
 *
 * Session info (opened at, session id), the table's orders with
 * their serve actions, and its open/acknowledged help calls. Data
 * comes from GET /staff/tables/:id via REST; the panel closes with
 * the × button (the selection lives in StaffPage).
 */
import { X } from "lucide-react";

import type { StaffTableDetail } from "@/shared/api/types";
import { ActiveOrders } from "@/features/staff/components/ActiveOrders";
import { HelpCallQueue } from "@/features/staff/components/HelpCallQueue";

function formatOpenedAt(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function TableDetail({
  detail,
  restaurantId,
  onClose,
  demo = false,
}: {
  detail: StaffTableDetail;
  restaurantId: string;
  onClose: () => void;
  demo?: boolean;
}) {
  const { table, session, orders, helpCalls } = detail;
  return (
    <section
      className="mt-4 rounded-xl border border-brand-700 bg-white p-4 shadow-sm"
      aria-label={`Table ${table.number} detail`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-xl font-bold">Table {table.number}</h2>
          <p className="mt-0.5 text-sm text-stone-600">
            {session
              ? `Session open since ${formatOpenedAt(session.openedAt)} · ${table.activeOrderCount} active order${table.activeOrderCount === 1 ? "" : "s"}`
              : "No active session — the table is free."}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close table detail"
          className="rounded-lg border border-stone-300 p-2 text-stone-600 transition hover:bg-stone-50"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="mt-4 grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <ActiveOrders
            orders={orders}
            restaurantId={restaurantId}
            demo={demo}
          />
        </div>
        <HelpCallQueue
          calls={helpCalls}
          restaurantId={restaurantId}
          demo={demo}
        />
      </div>
    </section>
  );
}
