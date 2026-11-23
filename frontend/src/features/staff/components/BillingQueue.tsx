/** Billing queue (Stage 6).
 *
 * The pending bill requests: payment_requested first (the waiter
 * needs to collect those), then issued, newest first — the order
 * the backend returns. Each row shows the table, bill number,
 * total, outstanding and request time. Selecting a row opens the
 * cash-collection detail. Paid bills are not in this queue: the
 * staff list endpoint returns them too (it is filterable, and a
 * history view may want them later), so this component drops any
 * paid row itself — a paid bill must never sit in the queue
 * mislabelled "Issued" with ₹0 outstanding.
 */
import { Banknote } from "lucide-react";

import type { StaffBillSummary } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";
import { cn } from "@/lib/utils";

function formatRequestTime(iso: string | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function BillingQueue({
  bills,
  selectedBillId,
  onSelect,
  demo = false,
}: {
  bills: StaffBillSummary[];
  selectedBillId?: string;
  onSelect: (billId: string) => void;
  demo?: boolean;
}) {
  // Paid bills leave the queue the moment staff confirm the cash
  // — see the header note. (The list endpoint still returns them;
  // a later history view can use them, this queue must not.)
  const openBills = bills.filter((bill) => bill.status !== "paid");
  return (
    <section aria-label="Billing">
      <h2 className="text-lg font-semibold">Billing</h2>
      {openBills.length === 0 ? (
        <p className="mt-3 rounded-xl border border-stone-200 bg-white p-6 text-sm text-stone-600">
          No bills awaiting cash.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {openBills.map((bill) => {
            const isRequested = bill.status === "payment_requested";
            const selected = bill.id === selectedBillId;
            return (
              <li key={bill.id}>
                <button
                  type="button"
                  onClick={() => onSelect(bill.id)}
                  aria-pressed={selected}
                  className={cn(
                    "w-full rounded-xl border bg-white p-4 text-left shadow-sm transition hover:border-brand-600",
                    selected
                      ? "border-brand-700 ring-1 ring-brand-700"
                      : "border-stone-200",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-semibold">
                        Table {bill.tableNumber} · {bill.billNumber}
                      </p>
                      <p className="mt-0.5 text-xs text-stone-500">
                        {bill.orderCount} order{bill.orderCount === 1 ? "" : "s"} ·{" "}
                        {isRequested
                          ? `payment requested ${formatRequestTime(bill.paymentRequestedAt)}`
                          : `issued ${formatRequestTime(bill.issuedAt)}`}
                      </p>
                    </div>
                    <span
                      className={cn(
                        "shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold",
                        isRequested
                          ? "bg-amber-100 text-amber-900"
                          : "bg-stone-100 text-stone-700",
                      )}
                    >
                      {isRequested ? "Payment requested" : "Issued"}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center justify-between text-sm">
                    <span className="flex items-center gap-1 text-stone-600">
                      <Banknote className="h-4 w-4" />
                      Outstanding
                    </span>
                    <span className="font-semibold tabular-nums">
                      {formatINR(bill.outstandingPaise)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-stone-500">
                    Total {formatINR(bill.totalPaise)}
                    {bill.amountPaidPaise > 0
                      ? ` · paid ${formatINR(bill.amountPaidPaise)}`
                      : ""}
                  </p>
                  {demo ? (
                    <p className="mt-2 text-xs font-medium text-amber-700">
                      DEMO — actions disabled
                    </p>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
