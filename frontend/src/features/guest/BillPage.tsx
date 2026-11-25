import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import {
  Banknote,
  CheckCircle2,
  HandPlatter,
  ReceiptText,
  Users,
} from "lucide-react";

import { ApiError } from "@/shared/api/client";
import type { Bill } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";
import { ReceiptView } from "@/shared/components/ReceiptView";
import {
  createBillSplit,
  requestBill,
  requestCashPayment,
} from "@/features/guest/api/guestApi";
import { useGuestBill, useGuestReceipt } from "@/features/guest/hooks/useGuest";
import { useGuestSocketStatus } from "@/features/guest/hooks/useGuestSocket";
import { GuestConnectionLine } from "@/features/guest/components/ConnectionIndicator";
import { equalSplit, validateCustomSplit } from "@/features/guest/lib/split";
import { EmptyState, LoadingState } from "@/features/guest/components/states";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Bill view — CASH ONLY in v1, live API only (Stage 6).
 *
 * - The bill comes from GET /guest/bill (the backend generates it
 *   from the session's unpaid orders). There is no cart-preview
 *   fallback any more: until the backend issues a bill, this page
 *   says so and offers "Request bill".
 * - Itemised bill: order numbers, items, variants, add-ons,
 *   quantities, unit prices, line totals, subtotal, tax
 *   "not configured" (decision D1 — never "0% GST"), total,
 *   amount paid, outstanding, payment status.
 * - Splits: equal (2/3/4, remainder rule from split.ts) or custom
 *   (amounts must sum exactly to the total). The preview is
 *   client-side only; the SERVER validates and is authoritative.
 * - Pay by Cash: PATCH /guest/bill/request-payment. It creates NO
 *   payment — the page then shows "Waiting for staff" until staff
 *   confirm the cash, and only then is the bill marked paid and
 *   the receipt shown. The bill refetches on focus and every 10s
 *   while unpaid, so the paid state appears without a refresh.
 * - Receipt: GET /guest/bill/receipt, rendered only after the bill
 *   is paid (the endpoint 409s before that).
 */
export function BillPage() {
  const { tableToken = "" } = useParams<{ tableToken: string }>();
  const billQuery = useGuestBill(tableToken);
  const bill: Bill | undefined = billQuery.data?.data;
  // Paid, refunded and partially_refunded bills all have a receipt
  // (the receipt endpoint serves all three — Stage 7).
  const isPaid =
    bill?.status === "paid" ||
    bill?.status === "refunded" ||
    bill?.status === "partially_refunded";
  const receiptQuery = useGuestReceipt(tableToken, isPaid === true);
  // Stage 8: a voided bill never comes back from GET /guest/bill
  // (it is neither open nor paid), so the void is carried by the
  // live ``bill.updated`` event: remember the voided bill id and
  // offer to request a new bill. A fresh bill clears the banner.
  const { lastEvent } = useGuestSocketStatus();
  const [voidedBillId, setVoidedBillId] = useState<string | null>(null);
  useEffect(() => {
    if (
      lastEvent?.type === "bill.updated" &&
      lastEvent.data.status === "voided" &&
      lastEvent.data.bill_id
    ) {
      setVoidedBillId(lastEvent.data.bill_id);
    }
  }, [lastEvent]);
  useEffect(() => {
    if (bill && voidedBillId && bill.id !== voidedBillId) {
      setVoidedBillId(null);
    }
  }, [bill, voidedBillId]);
  const showVoidedBanner = voidedBillId !== null && bill?.id !== voidedBillId;

  const [splitMode, setSplitMode] = useState<"equal" | "custom">("equal");
  const [equalParts, setEqualParts] = useState(2);
  const [customAmounts, setCustomAmounts] = useState<string[]>(["", ""]);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const totalPaise = bill?.totalPaise ?? 0;
  const billError = billQuery.error instanceof ApiError ? billQuery.error : null;
  const noBillYet = billError?.status === 404;

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
      await billQuery.refetch();
    } catch (err) {
      setActionError(err instanceof ApiError ? err : new ApiError(0, "request_failed", "Could not reach the backend."));
    } finally {
      setBusy(false);
    }
  };

  const handleRequestBill = () =>
    runAction(() => requestBill(tableToken), "Bill requested — here it is.");

  const handlePayByCash = () => {
    if (!bill) return;
    runAction(
      () => requestCashPayment(tableToken),
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
      () => createBillSplit(tableToken, split),
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
        Itemised, from your table&apos;s orders. Cash only — no UPI, cards or
        online payment in Tavessa v1.
      </p>
      <GuestConnectionLine />

      {showVoidedBanner ? (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 shadow-sm">
          <h2 className="text-sm font-semibold text-amber-800">
            This bill was voided — request a new bill
          </h2>
          <p className="mt-1 text-sm text-amber-800">
            The bill was voided by the restaurant (for example, the order
            changed). Nothing was charged. Your orders are still open —
            request a new bill and it is generated from them.
          </p>
          <Button
            className="mt-3"
            onClick={() => {
              setVoidedBillId(null);
              void handleRequestBill();
            }}
            disabled={busy}
          >
            {busy ? "Requesting…" : "Request a new bill"}
          </Button>
        </div>
      ) : null}

      {billQuery.isPending ? (
        <div className="mt-4"><LoadingState label="Loading your bill…" /></div>
      ) : noBillYet ? (
        <div className="mt-4 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
          <EmptyState
            title="No bill yet"
            body="Your bill appears here once you have placed an order. Ask for the bill and it is generated from your table's unpaid orders."
            action={
              <Button onClick={handleRequestBill} disabled={busy}>
                {busy ? "Requesting…" : "Request bill"}
              </Button>
            }
          />
          {actionError ? (
            <p className="mt-3 text-sm text-red-700">
              {actionError.code === "nothing_to_bill"
                ? "There is nothing to bill yet — place an order first."
                : actionError.message}
            </p>
          ) : null}
          {actionNote ? (
            <p className="mt-3 text-sm text-green-700">{actionNote}</p>
          ) : null}
        </div>
      ) : billError ? (
        <div className="mt-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          Could not load your bill: {billError.message}
          <div className="mt-3">
            <Button variant="outline" onClick={() => void billQuery.refetch()}>
              Retry
            </Button>
          </div>
        </div>
      ) : bill ? (
        <>
          {/* Itemised bill */}
          <div className="mt-5 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
            <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-stone-500">
              <ReceiptText className="h-4 w-4" />
              Bill {bill.billNumber}
              <span className="ml-auto rounded-full bg-stone-100 px-2 py-0.5 text-xs font-semibold normal-case text-stone-700">
                {bill.status === "payment_requested"
                  ? "Payment requested"
                  : bill.status}
              </span>
            </h2>
            {bill.orderNumbers.length > 0 ? (
              <p className="mt-2 text-xs text-stone-500">
                Orders: {bill.orderNumbers.join(", ")}
              </p>
            ) : null}

            <ul className="mt-3 space-y-2 text-sm">
              {bill.items.map((item) => (
                <li key={item.id} className="flex justify-between gap-2">
                  <span>
                    {item.quantity} × {item.name}
                    {item.variantName ? ` (${item.variantName})` : ""}
                    {item.addOnNames?.length
                      ? ` + ${item.addOnNames.join(", ")}`
                      : ""}
                    <span className="block text-xs text-stone-500">
                      {formatINR(item.unitPricePaise)} each
                    </span>
                  </span>
                  <span className="shrink-0 tabular-nums">{formatINR(item.totalPaise)}</span>
                </li>
              ))}
            </ul>
            <dl className="mt-3 space-y-1 border-t border-stone-200 pt-3 text-sm">
              <div className="flex justify-between">
                <dt>Subtotal</dt>
                <dd className="tabular-nums">{formatINR(bill.subtotalPaise)}</dd>
              </div>
              <div className="flex justify-between text-stone-500">
                <dt>
                  Tax
                  {bill.taxRateBpsSnapshot != null && bill.taxLabelSnapshot
                    ? ` (${bill.taxLabelSnapshot}, ${(bill.taxRateBpsSnapshot / 100).toFixed(1)}%)`
                    : ""}
                </dt>
                <dd>
                  {bill.taxRateBpsSnapshot != null
                    ? formatINR(bill.taxPaise)
                    : "Not configured yet (D1)"}
                </dd>
              </div>
              <div className="flex justify-between text-base font-semibold">
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
            {bill.splits.length > 0 ? (
              <div className="mt-3 border-t border-stone-200 pt-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-stone-500">
                  Splits
                </p>
                <ul className="mt-2 space-y-1.5 text-sm">
                  {bill.splits.map((split) => (
                    <li key={split.id} className="flex justify-between rounded-lg bg-stone-50 px-3 py-2">
                      <span>{split.label}</span>
                      <span className="tabular-nums">
                        {formatINR(split.amountPaise)} · {split.status.replace("_", " ")}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>

          {/* Split — only while the bill is unpaid */}
          {!isPaid && totalPaise > 0 ? (
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
                    The server re-checks the split before saving it.
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
                      Enter each person&apos;s share. The parts must add up to exactly {formatINR(totalPaise)}.
                    </p>
                  )}
                </div>
              )}

              <Button
                variant="outline"
                className="mt-4 w-full"
                onClick={handleApplySplit}
                disabled={busy || (splitMode === "custom" && customValidation?.valid === false)}
              >
                Save this split
              </Button>
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

            {bill.status === "payment_requested" || actionNote ? (
              <div className="mt-3 flex items-start gap-2 rounded-lg bg-green-50 px-3 py-2.5 text-sm text-green-800">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  {actionNote ??
                    "Payment requested — a waiter is on the way to collect the cash. The bill stays unpaid until staff confirm the collection."}
                </span>
              </div>
            ) : null}

            {actionError ? (
              <p className="mt-3 text-sm text-red-700">{actionError.message}</p>
            ) : null}

            {isPaid ? (
              <p className="mt-4 text-sm font-medium text-green-700">
                Paid — see your receipt below.
              </p>
            ) : bill.status === "payment_requested" ? (
              <p className="mt-4 text-sm text-stone-600">
                Waiting for staff — please pay cash at your table. The page
                updates itself once the waiter confirms the collection.
              </p>
            ) : (
              <Button size="lg" className="mt-4 w-full" onClick={handlePayByCash} disabled={busy}>
                {busy ? "Requesting…" : `Pay by Cash · ${formatINR(bill.outstandingPaise)}`}
              </Button>
            )}
          </div>

          {/* Receipt — only after a real paid bill exists */}
          {isPaid ? (
            <div className="mt-4">
              {receiptQuery.isPending ? (
                <LoadingState label="Loading your receipt…" />
              ) : receiptQuery.data ? (
                <ReceiptView receipt={receiptQuery.data.data} />
              ) : (
                <p className="text-sm text-stone-600">
                  Paid {formatINR(bill.amountPaidPaise)} in cash
                  {bill.paidAt ? ` on ${new Date(bill.paidAt).toLocaleString("en-IN")}` : ""}.
                </p>
              )}
            </div>
          ) : null}

          {/* Staff assistance — the existing help-call flow */}
          <div className="mt-4 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
            <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-stone-500">
              <HandPlatter className="h-4 w-4" />
              Need help?
            </h2>
            <p className="mt-2 text-sm text-stone-600">
              Ask a waiter to come to your table — for the bill, water,
              plates, or anything else.
            </p>
            <Button asChild variant="outline" className="mt-3">
              <Link to={`/t/${tableToken}/help`}>Request staff assistance</Link>
            </Button>
          </div>
        </>
      ) : null}
    </div>
  );
}
