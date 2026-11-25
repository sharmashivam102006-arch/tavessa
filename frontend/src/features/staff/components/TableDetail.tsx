/** Selected-table detail (Stages 5–6).
 *
 * Session info comes from the table row itself
 * (table.sessionId / table.sessionOpenedAt — GET
 * /staff/tables/:id does NOT send a separate `session` object;
 * Stage 9 fixed this panel reading a field the backend never
 * sent, which made every occupied table read "No active
 * session"). The table's orders with their serve actions, its
 * open/acknowledged help calls, and its open bill summary if one
 * exists (Stage 6). Data comes from GET /staff/tables/:id via
 * REST; the panel closes with the × button (the selection lives
 * in StaffPage).
 */
import { X } from "lucide-react";

import type { StaffTableDetail } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";
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
  const { table, orders, helpCalls, openBill } = detail;
  const sessionOpen = table.sessionId !== null && table.sessionOpenedAt !== null;
  return (
    <section
      className="mt-4 rounded-xl border border-brand-700 bg-white p-4 shadow-sm"
      aria-label={`Table ${table.number} detail`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-xl font-bold">Table {table.number}</h2>
          <p className="mt-0.5 text-sm text-stone-600">
            {sessionOpen
              ? `Session open since ${formatOpenedAt(table.sessionOpenedAt as string)} · ${table.activeOrderCount} active order${table.activeOrderCount === 1 ? "" : "s"}`
              : "No active session — the table is free."}
          </p>
          {openBill ? (
            <p className="mt-1 text-sm">
              Open bill {openBill.billNumber} · {openBill.status.replace("_", " ")} ·{" "}
              outstanding {formatINR(openBill.outstandingPaise)} of{" "}
              {formatINR(openBill.totalPaise)}
            </p>
          ) : null}
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
