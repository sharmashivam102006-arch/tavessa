/** Receipt view — a rendering of the receipt DATA returned by
 * GET /guest/bill/receipt or GET /staff/bills/:id/receipt
 * (Stage 6: JSON only, no PDF). Used by both the guest bill page
 * (after staff confirm the cash) and the staff billing detail.
 * The receipt is read-only and is only ever rendered for a paid
 * bill — both receipt endpoints 409 until the bill is paid.
 */
import { ReceiptText } from "lucide-react";

import type { Receipt } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ReceiptView({ receipt }: { receipt: Receipt }) {
  return (
    <div className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
      <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-stone-500">
        <ReceiptText className="h-4 w-4" />
        Receipt
      </h2>
      <p className="mt-3 text-lg font-semibold">{receipt.restaurantName}</p>
      {receipt.branchName ? (
        <p className="text-sm text-stone-600">{receipt.branchName}</p>
      ) : null}
      <dl className="mt-3 space-y-1 text-sm">
        <div className="flex justify-between">
          <dt className="text-stone-500">Receipt</dt>
          <dd className="font-medium">{receipt.receiptNumber}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-stone-500">Bill</dt>
          <dd className="font-medium">{receipt.billNumber}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-stone-500">Table</dt>
          <dd className="font-medium">{receipt.tableNumber}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-stone-500">Orders</dt>
          <dd className="font-medium">{receipt.orderNumbers.join(", ")}</dd>
        </div>
        {receipt.receiptTimestamp ? (
          <div className="flex justify-between">
            <dt className="text-stone-500">Paid at</dt>
            <dd className="font-medium">
              {formatTimestamp(receipt.receiptTimestamp)}
            </dd>
          </div>
        ) : null}
      </dl>

      <ul className="mt-3 space-y-2 border-t border-stone-200 pt-3 text-sm">
        {receipt.items.map((item) => (
          <li key={item.id} className="flex justify-between gap-2">
            <span>
              {item.quantity} × {item.name}
              {item.variantName ? ` (${item.variantName})` : ""}
              {item.addOnNames?.length
                ? ` + ${item.addOnNames.join(", ")}`
                : ""}
            </span>
            <span className="shrink-0 tabular-nums">
              {formatINR(item.totalPaise)}
            </span>
          </li>
        ))}
      </ul>

      <dl className="mt-3 space-y-1 border-t border-stone-200 pt-3 text-sm">
        <div className="flex justify-between">
          <dt>Subtotal</dt>
          <dd className="tabular-nums">{formatINR(receipt.subtotalPaise)}</dd>
        </div>
        <div className="flex justify-between text-stone-500">
          <dt>Tax</dt>
          <dd>{receipt.taxStatus}</dd>
        </div>
        <div className="flex justify-between text-base font-semibold">
          <dt>Total</dt>
          <dd className="tabular-nums">{formatINR(receipt.totalPaise)}</dd>
        </div>
        <div className="flex justify-between text-green-700">
          <dt>Amount received (Cash)</dt>
          <dd className="tabular-nums">
            {formatINR(receipt.amountReceivedPaise)}
          </dd>
        </div>
        <div className="flex justify-between">
          <dt>Outstanding</dt>
          <dd className="tabular-nums">
            {formatINR(receipt.outstandingPaise)}
          </dd>
        </div>
      </dl>

      <p className="mt-3 text-xs text-stone-500">
        Paid in cash
        {receipt.paymentTimestamps.length > 0
          ? ` — ${receipt.paymentTimestamps.map((t) => formatTimestamp(t)).join(", ")}`
          : ""}
        . Thank you for dining with us.
      </p>
    </div>
  );
}
