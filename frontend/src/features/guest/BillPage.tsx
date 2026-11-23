import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { Banknote, CheckCircle2, ReceiptText, Users } from "lucide-react";

import { ApiError } from "@/shared/api/client";
import type { Bill } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";
import {
  createBillSplit,
  requestBill,
  requestCashPayment,
} from "@/features/guest/api/guestApi";
import { useGuestBill } from "@/features/guest/hooks/useGuest";
import { equalSplit, validateCustomSplit } from "@/features/guest/lib/split";
import { priceForConfiguration } from "@/features/guest/lib/pricing";
import { useCartStore } from "@/features/guest/stores/cartStore";
import { findMenuItem } from "@/features/guest/data/zaika-menu";
import { BackendNotConnected, EmptyState, LoadingState } from "@/features/guest/components/states";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Bill view — CASH ONLY in v1.
 *
 * - Itemised bill: from the real bill when GET /guest/bill returns
 *   one; otherwise an estimated preview built from the cart, clearly
 *   labelled as a preview (the backend generates the bill from unpaid
 *   orders in Stage 6).
 * - Taxes: not configured (decision D1) — no tax line is invented.
 * - Splits: equal (2/3/4, remainder rule from split.ts) or custom
 *   (amounts must sum exactly to the total). Splits are only sent to
 *   the backend once a real bill exists.
 * - Pay by Cash: requests payment (PATCH /bills/:id/request-payment).
 *   It creates NO payment. Only staff can confirm cash collection
 *   (POST /bills/:id/payments, staff JWT). The receipt preview below
 *   renders only after a real paid bill exists.
 */
export function BillPage() {
  const { tableToken = "" } = useParams<{ tableToken: string }>();
  const billQuery = useGuestBill(tableToken);
  const bill: Bill | undefined = billQuery.data?.data;
  const lines = useCartStore((s) => s.lines);
  const estimatedSubtotal = useCartStore((s) => s.estimatedSubtotal(findMenuItem));

  const [splitMode, setSplitMode] = useState<"equal" | "custom">("equal");
  const [equalParts, setEqualParts] = useState(2);
  const [customAmounts, setCustomAmounts] = useState<string[]>(["", ""]);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The amount the split UI works on: the real bill total, or the
  // cart estimate when no bill exists yet.
  const totalPaise = bill ? bill.totalPaise : estimatedSubtotal;
  const isPreview = !bill;

  const equalPartsAmounts = useMemo(
    () => (totalPaise > 0 ? equalSplit(totalPaise, equalParts) : []),
    [totalPaise, equalParts],
  );
  const customParsed = useMemo(
    () => customAmounts.map((a) => Math.round(Number(a) * 100)),
    [customAmounts],
  );
  const customValidation = useMemo(
    () =>
      splitMode === "custom" && customAmounts.every((a) => a.trim() !== "")
        ? validateCustomSplit(customParsed, totalPaise)
        : null,
    [splitMode, customAmounts, customParsed, totalPaise],
  );

  const runAction = async (fn: () => Promise<unknown>, successNote: string) => {
    setBusy(true);
    setActionError(null);
    setActionNote(null);
    try {
      await fn();
      setActionNote(successNote);
      billQuery.refetch();
    } catch (err) {
      setActionError(err instanceof ApiError ? err : new ApiError(0, "request_failed", "Could not reach the backend."));
    } finally {
      setBusy(false);
    }
  };

  const handleRequestBill = () =>
    runAction(() => requestBill(tableToken), "Bill requested — it will appear here once the backend generates it.");

  const handlePayByCash = () => {
    if (!bill) return;
    runAction(
      () => requestCashPayment(tableToken, bill.id),
      "Payment requested. A waiter is on the way to collect the cash — the bill is marked paid only after staff confirm the collection.",
    );
  };

  const handleApplySplit = () => {
    if (!bill) return;
    const split =
      splitMode === "equal"
        ? { mode: "equal" as const, parts: equalParts }
        : { mode: "custom" as const, amountsPaise: customParsed };
    runAction(
      () => createBillSplit(tableToken, bill.id, split),
      "Split saved. Each part is paid separately, in cash, confirmed by staff.",
    );
  };

  const setCustomAmount = (index: number, value: string) => {
    setCustomAmounts((prev) => prev.map((v, i) => (i === index ? value : v)));
  };
  const setCustomPartCount = (count: number) => {
    setCustomAmounts((prev) => {
      const next = [...prev];
      while (next.length < count) next.push("");
      return next.slice(0, count);
    });
  };

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Your bill</h1>
      <p className="mt-1 text-sm text-stone-600">
        Itemised, from your table's orders. Cash only — no UPI, cards or
        online payment in Tavessa v1.
      </p>

      {billQuery.isPending ? (
        <div className="mt-4"><LoadingState label="Loading your bill…" /></div>
      ) : isPreview ? (
        <div className="mt-4">
          <BackendNotConnected
            endpoint="GET /api/v1/guest/bill"
            stage="Stage 6 (billing & cash payments)"
            what="Loading the real bill for this table"
          >
            <p className="mt-2 text-sm text-amber-800">
              Below is a preview built from your cart, so you can see the
              layout. It is an estimate — the real bill is generated by the
              backend from your unpaid orders.
            </p>
          </BackendNotConnected>
        </div>
      ) : null}

      {/* Itemised bill / preview */}
      <div className="mt-5 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
        <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-stone-500">
          <ReceiptText className="h-4 w-4" />
          {isPreview ? "Bill preview (from your cart)" : `Bill ${bill?.id.slice(0, 8)}…`}
          {bill ? (
            <span className="ml-auto rounded-full bg-stone-100 px-2 py-0.5 text-xs font-semibold normal-case text-stone-700">
              {bill.status.replace("_", " ")}
            </span>
          ) : null}
        </h2>

        {isPreview && lines.length === 0 ? (
          <div className="mt-3">
            <EmptyState
              title="Nothing on the bill yet"
              body="Your bill appears here once you have placed an order. Browse the menu to get started."
              action={
                <Button asChild>
                  <Link to={`/t/${tableToken}`}>Browse the menu</Link>
                </Button>
              }
            />
          </div>
        ) : (
          <>
            <ul className="mt-3 space-y-2 text-sm">
              {isPreview
                ? lines.map((line) => {
                    const item = findMenuItem(line.menuItemId);
                    if (!item) return null;
                    const unit = priceForConfiguration(item, line.variantId, line.addOnIds);
                    return (
                      <li key={line.key} className="flex justify-between gap-2">
                        <span>
                          {line.quantity} × {item.name}
                        </span>
                        <span className="shrink-0 tabular-nums">{formatINR(unit * line.quantity)}</span>
                      </li>
                    );
                  })
                : bill?.items.map((item) => (
                    <li key={item.id} className="flex justify-between gap-2">
                      <span>
                        {item.quantity} × {item.name}
                      </span>
                      <span className="shrink-0 tabular-nums">{formatINR(item.totalPaise)}</span>
                    </li>
                  ))}
            </ul>
            <dl className="mt-3 space-y-1 border-t border-stone-200 pt-3 text-sm">
              <div className="flex justify-between">
                <dt>Subtotal</dt>
                <dd className="tabular-nums">
                  {formatINR(bill ? bill.subtotalPaise : estimatedSubtotal)}
                </dd>
              </div>
              <div className="flex justify-between text-stone-500">
                <dt>Taxes</dt>
                <dd>
                  {bill && bill.taxPaise ? formatINR(bill.taxPaise) : "Not configured yet (D1)"}
                </dd>
              </div>
              <div className="flex justify-between text-base font-semibold">
                <dt>Total {isPreview ? "(estimated)" : ""}</dt>
                <dd className="tabular-nums">{formatINR(totalPaise)}</dd>
              </div>
              {bill && bill.amountPaidPaise > 0 ? (
                <div className="flex justify-between text-green-700">
                  <dt>Paid</dt>
                  <dd className="tabular-nums">{formatINR(bill.amountPaidPaise)}</dd>
                </div>
              ) : null}
            </dl>
          </>
        )}
      </div>

      {/* Split */}
      {totalPaise > 0 ? (
        <div className="mt-4 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
          <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-stone-500">
            <Users className="h-4 w-4" />
            Split the bill
          </h2>
          <div className="mt-3 flex gap-2">
            {(["equal", "custom"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setSplitMode(mode)}
                aria-pressed={splitMode === mode}
                className={cn(
                  "flex-1 rounded-lg border px-3 py-2 text-sm font-medium",
                  splitMode === mode
                    ? "border-brand-600 bg-brand-50 text-brand-700"
                    : "border-stone-300 text-stone-700",
                )}
              >
                {mode === "equal" ? "Split equally" : "Custom amounts"}
              </button>
            ))}
          </div>

          {splitMode === "equal" ? (
            <div className="mt-3">
              <div className="flex gap-2">
                {[2, 3, 4].map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setEqualParts(n)}
                    aria-pressed={equalParts === n}
                    className={cn(
                      "flex-1 rounded-lg border px-3 py-2 text-sm font-medium",
                      equalParts === n
                        ? "border-brand-600 bg-brand-700 text-white"
                        : "border-stone-300 text-stone-700",
                    )}
                  >
                    {n} ways
                  </button>
                ))}
              </div>
              <ul className="mt-3 space-y-1.5 text-sm">
                {equalPartsAmounts.map((amount, i) => (
                  <li key={i} className="flex justify-between rounded-lg bg-stone-50 px-3 py-2">
                    <span>Person {i + 1}</span>
                    <span className="font-medium tabular-nums">{formatINR(amount)}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-stone-500">
                Odd paise are distributed 1p each to the earliest parts, so
                the parts always add up to exactly {formatINR(totalPaise)}.
              </p>
            </div>
          ) : (
            <div className="mt-3">
              <div className="flex gap-2">
                {[2, 3, 4].map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setCustomPartCount(n)}
                    aria-pressed={customAmounts.length === n}
                    className={cn(
                      "flex-1 rounded-lg border px-3 py-2 text-sm font-medium",
                      customAmounts.length === n
                        ? "border-brand-600 bg-brand-700 text-white"
                        : "border-stone-300 text-stone-700",
                    )}
                  >
                    {n} parts
                  </button>
                ))}
              </div>
              <div className="mt-3 space-y-2">
                {customAmounts.map((value, i) => (
                  <label key={i} className="flex items-center gap-2 text-sm">
                    <span className="w-20 shrink-0">Person {i + 1}</span>
                    <span className="text-stone-500">₹</span>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      inputMode="decimal"
                      value={value}
                      onChange={(e) => setCustomAmount(i, e.target.value)}
                      placeholder="0.00"
                      className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm tabular-nums outline-none focus:border-brand-600 focus:ring-1 focus:ring-brand-600"
                    />
                  </label>
                ))}
              </div>
              {customValidation ? (
                <p className={cn("mt-2 text-xs", customValidation.valid ? "text-green-700" : "text-red-700")}>
                  {customValidation.valid
                    ? `The parts add up to exactly ${formatINR(totalPaise)}.`
                    : customValidation.error}
                </p>
              ) : (
                <p className="mt-2 text-xs text-stone-500">
                  Enter each person's share. The parts must add up to exactly {formatINR(totalPaise)}.
                </p>
              )}
            </div>
          )}

          {bill ? (
            <Button
              variant="outline"
              className="mt-4 w-full"
              onClick={handleApplySplit}
              disabled={busy || (splitMode === "custom" && customValidation?.valid === false)}
            >
              Save this split
            </Button>
          ) : (
            <p className="mt-3 text-xs text-stone-500">
              Splits are saved to the bill once the backend issues it (Stage 6).
              The amounts above are a preview.
            </p>
          )}
        </div>
      ) : null}

      {/* Pay by Cash */}
      <div className="mt-4 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
        <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-stone-500">
          <Banknote className="h-4 w-4" />
          Pay by Cash
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-stone-600">
          Tavessa is cash-only in v1. Tapping “Pay by Cash” only asks a
          waiter to come and collect the cash — it does <strong>not</strong>{" "}
          mark anything paid. A waiter confirms the collection at your
          table, and only then is the bill marked paid and a receipt issued.
        </p>

        {bill?.status === "payment_requested" || actionNote ? (
          <div className="mt-3 flex items-start gap-2 rounded-lg bg-green-50 px-3 py-2.5 text-sm text-green-800">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              {actionNote ??
                "Payment requested — a waiter is on the way to collect the cash. The bill stays unpaid until staff confirm the collection."}
            </span>
          </div>
        ) : null}

        {actionError ? (
          <div className="mt-3">
            <BackendNotConnected
              endpoint={bill ? `PATCH /api/v1/bills/${bill.id}/request-payment` : "POST /api/v1/bills"}
              stage="Stage 6 (billing & cash payments)"
              what={bill ? "Requesting cash payment" : "Requesting your bill"}
            >
              <p className="mt-2 text-sm text-amber-800">
                The backend answered {actionError.status} ({actionError.code}).
                Nothing was requested — please ask a waiter, or retry once the
                backend is connected.
              </p>
            </BackendNotConnected>
          </div>
        ) : null}

        {!bill ? (
          <Button size="lg" className="mt-4 w-full" onClick={handleRequestBill} disabled={busy || lines.length === 0}>
            {busy ? "Requesting…" : "Request bill"}
          </Button>
        ) : bill.status === "paid" ? (
          <p className="mt-4 text-sm font-medium text-green-700">
            Paid — see your receipt below.
          </p>
        ) : (
          <Button size="lg" className="mt-4 w-full" onClick={handlePayByCash} disabled={busy}>
            {busy ? "Requesting…" : `Pay by Cash · ${formatINR(totalPaise)}`}
          </Button>
        )}
      </div>

      {/* Receipt — only after a real paid bill exists */}
      {bill?.status === "paid" ? (
        <div className="mt-4 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">Receipt</h2>
          <p className="mt-2 text-sm">
            Receipt {bill.receiptNumber ? <strong>{bill.receiptNumber}</strong> : "(number issued by the backend)"}
          </p>
          <p className="text-sm text-stone-600">
            Paid {formatINR(bill.amountPaidPaise)} in cash
            {bill.paidAt ? ` on ${new Date(bill.paidAt).toLocaleString("en-IN")}` : ""}.
          </p>
          <p className="mt-1 text-xs text-stone-500">
            Thank you for dining at Zaika. This receipt was issued only after
            staff confirmed the cash collection.
          </p>
        </div>
      ) : null}
    </div>
  );
}
