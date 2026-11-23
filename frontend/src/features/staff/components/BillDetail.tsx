/** Bill detail + cash collection (Stage 6).
 *
 * Shows the full bill (items, splits, payment history, totals,
 * outstanding) and the cash-collection screen:
 * - the amount input is prefilled with the outstanding amount
 *   (whole bill, or the selected split's outstanding amount);
 * - the amount is validated client-side (> 0 and <= outstanding)
 *   and re-validated by the server (422 otherwise);
 * - tap "Record cash" opens an explicit confirm panel showing the
 *   exact amount and target — only Confirm sends the payment;
 * - on success the receipt is shown and the bill leaves the
 *   pending queue (the parent's bills query is invalidated);
 * - on failure the error shows and the bill stays in place.
 *
 * Cash only — there is no online-payment button anywhere. In demo
 * mode every action is disabled.
 */
import { useEffect, useMemo, useState } from "react";

import { ApiError } from "@/shared/api/client";
import type { Bill, Receipt } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";
import { ReceiptView } from "@/shared/components/ReceiptView";
import {
  useRecordPayment,
  useStaffBillReceipt,
} from "@/features/staff/hooks/useStaff";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function splitPaidAmount(bill: Bill, splitId: string): number {
  return bill.payments
    .filter((p) => p.billSplitId === splitId)
    .reduce((sum, p) => sum + p.amountPaise, 0);
}

export function BillDetail({
  bill,
  restaurantId,
  demo = false,
}: {
  bill: Bill;
  restaurantId: string;
  demo?: boolean;
}) {
  const recordPayment = useRecordPayment(restaurantId);
  const receiptQuery = useStaffBillReceipt(
    restaurantId,
    bill.id,
    bill.status === "paid",
  );

  const hasSplits = bill.splits.length > 0;
  const [targetSplitId, setTargetSplitId] = useState<string | "whole">(
    "whole",
  );
  // The amount input, in rupees (display) — converted to paise on
  // submit. Prefilled with the outstanding amount of the target.
  const [amountRupees, setAmountRupees] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmedReceipt, setConfirmedReceipt] = useState<Receipt | null>(null);

  const duePaise = useMemo(() => {
    if (targetSplitId === "whole") return bill.outstandingPaise;
    const split = bill.splits.find((s) => s.id === targetSplitId);
    if (!split) return 0;
    return split.amountPaise - splitPaidAmount(bill, split.id);
  }, [bill, targetSplitId]);

  // Prefill (and re-prefill when the target or the bill changes,
  // e.g. after a partial payment) — but never while the confirm
  // panel is open, so an in-flight confirmation is not rewritten.
  useEffect(() => {
    if (!confirming) {
      setAmountRupees((duePaise / 100).toFixed(2));
    }
  }, [duePaise, confirming]);

  const amountPaise = Math.round(Number(amountRupees) * 100);
  const amountValid =
    Number.isFinite(amountPaise) && amountPaise > 0 && amountPaise <= duePaise;
  const amountError =
    amountRupees.trim() === ""
      ? null
      : !Number.isFinite(amountPaise) || amountPaise <= 0
        ? "Enter an amount above ₹0."
        : amountPaise > duePaise
          ? `That is more than the ${formatINR(duePaise)} due.`
          : null;

  const handleConfirm = async () => {
    if (demo || !amountValid) return;
    setError(null);
    try {
      const result = await recordPayment.mutateAsync({
        billId: bill.id,
        amountPaise,
        billSplitId: targetSplitId === "whole" ? undefined : targetSplitId,
        note: note.trim() ? note.trim() : undefined,
      });
      if (result.data.receipt) {
        setConfirmedReceipt(result.data.receipt);
      }
      setConfirming(false);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not record the payment. The bill is unchanged — try again.",
      );
      setConfirming(false);
    }
  };

  const receipt = confirmedReceipt ?? receiptQuery.data?.data ?? null;
  const isPaid = bill.status === "paid";
  // Show the receipt as soon as this panel confirmed a payment,
  // even before the parent's refetch hands us the updated bill.
  const showReceipt = isPaid || confirmedReceipt !== null;

  return (
    <section
      aria-label={`Bill ${bill.billNumber}`}
      className="rounded-xl border border-brand-700 bg-white p-4 shadow-sm"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-xl font-bold">
            Table {bill.tableNumber} · {bill.billNumber}
          </h2>
          <p className="mt-0.5 text-sm text-stone-600">
            {bill.orderNumbers.length > 0
              ? `Orders: ${bill.orderNumbers.join(", ")} · `
              : ""}
            {bill.status === "payment_requested"
              ? "Payment requested by the guest"
              : bill.status}
          </p>
        </div>
        <span
          className={cn(
            "shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold",
            isPaid
              ? "bg-green-100 text-green-900"
              : bill.status === "payment_requested"
                ? "bg-amber-100 text-amber-900"
                : "bg-stone-100 text-stone-700",
          )}
        >
          {isPaid ? "Paid" : bill.status.replace("_", " ")}
        </span>
      </div>

      <ul className="mt-4 space-y-2 text-sm">
        {bill.items.map((item) => (
          <li key={item.id} className="flex justify-between gap-2">
            <span>
              {item.quantity} × {item.name}
              {item.variantName ? ` (${item.variantName})` : ""}
              {item.addOnNames?.length ? ` + ${item.addOnNames.join(", ")}` : ""}
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
          <dd className="tabular-nums">{formatINR(bill.subtotalPaise)}</dd>
        </div>
        <div className="flex justify-between text-stone-500">
          <dt>Tax</dt>
          <dd>Not configured yet (D1)</dd>
        </div>
        <div className="flex justify-between font-semibold">
          <dt>Total</dt>
          <dd className="tabular-nums">{formatINR(bill.totalPaise)}</dd>
        </div>
        {bill.amountPaidPaise > 0 ? (
          <div className="flex justify-between text-green-700">
            <dt>Paid</dt>
            <dd className="tabular-nums">{formatINR(bill.amountPaidPaise)}</dd>
          </div>
        ) : null}
        <div className="flex justify-between font-semibold">
          <dt>Outstanding</dt>
          <dd className="tabular-nums">{formatINR(bill.outstandingPaise)}</dd>
        </div>
      </dl>

      {hasSplits ? (
        <div className="mt-3 border-t border-stone-200 pt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
            Splits
          </p>
          <ul className="mt-2 space-y-1.5 text-sm">
            {bill.splits.map((split) => {
              const outstanding =
                split.amountPaise - splitPaidAmount(bill, split.id);
              return (
                <li
                  key={split.id}
                  className="flex justify-between rounded-lg bg-stone-50 px-3 py-2"
                >
                  <span>{split.label}</span>
                  <span className="tabular-nums">
                    {formatINR(split.amountPaise)} ·{" "}
                    {split.status === "paid"
                      ? "paid"
                      : `outstanding ${formatINR(outstanding)}`}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {bill.payments.length > 0 ? (
        <div className="mt-3 border-t border-stone-200 pt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
            Payments
          </p>
          <ul className="mt-2 space-y-1.5 text-sm">
            {bill.payments.map((payment) => (
              <li
                key={payment.id}
                className="flex justify-between rounded-lg bg-green-50 px-3 py-2 text-green-900"
              >
                <span>
                  Cash · {payment.receiptNumber}
                </span>
                <span className="tabular-nums">
                  {formatINR(payment.amountPaise)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {showReceipt ? (
        <div className="mt-4">
          {receipt ? (
            <ReceiptView receipt={receipt} />
          ) : (
            <p className="text-sm font-medium text-green-700">
              Paid in full — receipt {bill.payments.at(-1)?.receiptNumber ?? ""}.
            </p>
          )}
        </div>
      ) : (
        <div className="mt-4 rounded-xl border border-stone-200 bg-stone-50 p-4">
          <h3 className="text-sm font-semibold">Collect cash</h3>
          <p className="mt-1 text-xs text-stone-500">
            Record the cash only after actually collecting it at the
            table. This is the only path to “paid”.
          </p>

          {hasSplits ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {bill.splits
                .filter((s) => s.status !== "paid")
                .map((split) => (
                  <button
                    key={split.id}
                    type="button"
                    onClick={() => setTargetSplitId(split.id)}
                    aria-pressed={targetSplitId === split.id}
                    className={cn(
                      "rounded-lg border px-3 py-2 text-sm font-medium",
                      targetSplitId === split.id
                        ? "border-brand-600 bg-brand-700 text-white"
                        : "border-stone-300 bg-white text-stone-700",
                    )}
                  >
                    {split.label} ·{" "}
                    {formatINR(split.amountPaise - splitPaidAmount(bill, split.id))}
                  </button>
                ))}
            </div>
          ) : null}

          <label className="mt-3 flex items-center gap-2 text-sm">
            <span className="w-24 shrink-0">Amount (₹)</span>
            <input
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              value={amountRupees}
              onChange={(e) => setAmountRupees(e.target.value)}
              disabled={demo || confirming}
              className="w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm tabular-nums outline-none focus:border-brand-600 focus:ring-1 focus:ring-brand-600"
            />
          </label>
          {amountError ? (
            <p className="mt-2 text-xs text-red-700">{amountError}</p>
          ) : (
            <p className="mt-2 text-xs text-stone-500">
              Outstanding on this target: {formatINR(duePaise)}. The
              amount cannot exceed it.
            </p>
          )}

          <label className="mt-3 block text-sm">
            <span className="text-stone-600">Note (optional)</span>
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={demo || confirming}
              placeholder="e.g. collected at the table"
              maxLength={500}
              className="mt-1 w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand-600 focus:ring-1 focus:ring-brand-600"
            />
          </label>

          {error ? (
            <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">
              {error}
            </p>
          ) : null}

          {!confirming ? (
            <Button
              size="lg"
              className="mt-4 w-full"
              onClick={() => setConfirming(true)}
              disabled={demo || !amountValid || recordPayment.isPending}
            >
              {demo
                ? "Record cash (disabled in demo)"
                : `Record cash · ${formatINR(amountPaise)}`}
            </Button>
          ) : (
            <div
              className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3"
              role="alert"
            >
              <p className="text-sm font-semibold text-amber-900">
                Confirm cash collection
              </p>
              <p className="mt-1 text-sm text-amber-900">
                Collect <strong>{formatINR(amountPaise)}</strong> in cash
                {targetSplitId !== "whole"
                  ? ` for ${bill.splits.find((s) => s.id === targetSplitId)?.label ?? "this split"}`
                  : ` for bill ${bill.billNumber}`}
                , then confirm. This marks the amount paid and issues
                a receipt number.
              </p>
              <div className="mt-3 flex gap-2">
                <Button
                  className="flex-1"
                  onClick={() => void handleConfirm()}
                  disabled={recordPayment.isPending}
                >
                  {recordPayment.isPending ? "Recording…" : "Confirm"}
                </Button>
                <Button
                  variant="outline"
                  className="flex-1"
                  onClick={() => setConfirming(false)}
                  disabled={recordPayment.isPending}
                >
                  Back
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
