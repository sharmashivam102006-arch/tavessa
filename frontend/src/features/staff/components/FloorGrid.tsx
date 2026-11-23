/** The floor grid — one card per table, in number order (Stage 5
 * uses a simple grid; no position_x/y coordinates are seeded).
 *
 * Each card shows the big table number, the table's own status
 * badge, and live operational badges: order count, READY (green)
 * when at least one order is ready to serve, and NEEDS HELP (red)
 * when an open help call is waiting. Clicking a card selects the
 * table (detail below the grid); the selected card is outlined.
 */
import { ORDER_STATUS_LABELS } from "@/shared/api/types";
import type { StaffTable } from "@/shared/api/types";
import { statusBadgeForTable } from "@/features/staff/lib/staff";

const STATUS_BORDER: Record<string, string> = {
  available: "border-stone-200",
  occupied: "border-amber-300",
  cleaning: "border-sky-300",
  out_of_service: "border-red-300",
};

export function FloorGrid({
  tables,
  selectedTableId,
  onSelect,
}: {
  tables: StaffTable[];
  selectedTableId: string | undefined;
  onSelect: (tableId: string) => void;
}) {
  return (
    <div
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5"
      role="list"
      aria-label="Floor — tables"
    >
      {tables.map((table) => {
        const badge = statusBadgeForTable(table.status);
        const selected = table.id === selectedTableId;
        return (
          <div key={table.id} role="listitem">
          <button
            type="button"
            aria-pressed={selected}
            aria-label={`Table ${table.number}, ${badge.label}${
              table.activeOrderCount > 0
                ? `, ${table.activeOrderCount} active order${table.activeOrderCount > 1 ? "s" : ""}`
                : ""
            }${table.readyOrderCount > 0 ? ", ready to serve" : ""}${
              table.hasOpenHelpCall ? ", needs help" : ""
            }`}
            onClick={() => onSelect(table.id)}
            className={`w-full rounded-xl border-2 bg-white p-4 text-left shadow-sm transition hover:shadow-md ${
              STATUS_BORDER[table.status] ?? "border-stone-200"
            } ${selected ? "ring-2 ring-brand-700 ring-offset-2" : ""}`}
          >
            <span className="flex items-baseline justify-between gap-2">
              <span className="text-3xl font-bold tracking-tight">
                {table.number}
              </span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-semibold ${badge.className}`}
              >
                {badge.label}
              </span>
            </span>
            <span className="mt-2 flex flex-wrap gap-1.5">
              {table.activeOrderCount > 0 ? (
                <span className="rounded-full bg-stone-100 px-2 py-0.5 text-xs font-semibold text-stone-700">
                  {table.activeOrderCount} order
                  {table.activeOrderCount > 1 ? "s" : ""}
                  {table.latestOrderStatus
                    ? ` · ${ORDER_STATUS_LABELS[table.latestOrderStatus]}`
                    : ""}
                </span>
              ) : null}
              {table.readyOrderCount > 0 ? (
                <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-semibold text-green-900">
                  READY
                </span>
              ) : null}
              {table.hasOpenHelpCall ? (
                <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-900">
                  NEEDS HELP
                </span>
              ) : null}
              {table.activeOrderCount === 0 && !table.hasOpenHelpCall ? (
                <span className="rounded-full bg-stone-50 px-2 py-0.5 text-xs font-medium text-stone-500">
                  No orders
                </span>
              ) : null}
            </span>
          </button>
          </div>
        );
      })}
    </div>
  );
}
