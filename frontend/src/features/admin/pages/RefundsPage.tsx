/** Refunds — Stage 7 § H. List + create form (payment, amount,
 * reason) with a confirmation step. The original payment is never
 * deleted or overwritten; a refund only adjusts statuses. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api/client";
import {
  createRefund,
  fetchAdminPayments,
  fetchAdminRefunds,
} from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/features/admin/components/AdminBits";
import { formatINR } from "@/shared/utils/money";

export function RefundsPage() {
  const queryClient = useQueryClient();
  const [paymentId, setPaymentId] = useState("");
  const [amountRupees, setAmountRupees] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refundsQuery = useQuery({
    queryKey: ["admin-refunds"],
    queryFn: () => fetchAdminRefunds(1),
    retry: false,
  });
  const paymentsQuery = useQuery({
    queryKey: ["admin-payments"],
    queryFn: () => fetchAdminPayments(1),
    retry: false,
  });
  const create = useMutation({
    mutationFn: () =>
      createRefund(
        paymentsQuery.data?.data.find((p) => p.id === paymentId)?.billId ?? "",
        paymentId,
        Math.round(Number(amountRupees) * 100),
        reason.trim(),
      ),
    onSuccess: (refund) => {
      setMessage(`Refund ${refund.refundReference} recorded for ${formatINR(refund.amountPaise)}.`);
      setConfirming(false);
      setPaymentId("");
      setAmountRupees("");
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["admin-refunds"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-payments"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-bills"] });
    },
    onError: (error) => {
      setConfirming(false);
      setMessage(error instanceof ApiError ? error.message : "Could not record the refund.");
    },
  });

  const payments = paymentsQuery.data?.data ?? [];
  const selected = payments.find((p) => p.id === paymentId);

  return (
    <div>
      <PageHeader title="Refunds" sub="Cash refunds against confirmed payments." />
      {message && (
        <p role="status" className="mb-3 rounded border border-stone-300 bg-white p-3 text-sm">
          {message}
        </p>
      )}
      <form
        className="mb-6 flex flex-wrap gap-2 rounded-lg border border-stone-200 bg-white p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (paymentId && Number(amountRupees) > 0 && reason.trim()) setConfirming(true);
        }}
      >
        <select
          aria-label="Payment to refund"
          value={paymentId}
          onChange={(e) => setPaymentId(e.target.value)}
          className="rounded border border-stone-300 px-2 py-1.5 text-sm"
        >
          <option value="">Payment…</option>
          {payments.map((p) => (
            <option key={p.id} value={p.id}>
              {p.receiptNumber} · {p.billNumber} · {formatINR(p.amountPaise)}
            </option>
          ))}
        </select>
        <input
          aria-label="Refund amount in rupees"
          value={amountRupees}
          onChange={(e) => setAmountRupees(e.target.value)}
          placeholder="Amount ₹"
          inputMode="decimal"
          className="w-28 rounded border border-stone-300 px-2 py-1.5 text-sm"
        />
        <input
          aria-label="Refund reason"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason"
          className="min-w-48 flex-1 rounded border border-stone-300 px-2 py-1.5 text-sm"
        />
        <button className="rounded bg-stone-900 px-3 py-1.5 text-sm text-white">Review refund</button>
      </form>
      {confirming && selected && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/40" role="dialog" aria-label="Confirm refund">
          <div className="w-full max-w-md rounded-lg bg-white p-5">
            <h2 className="text-lg font-semibold">Confirm refund?</h2>
            <p className="mt-2 text-sm text-stone-600">
              Refund {formatINR(Math.round(Number(amountRupees) * 100))} against {selected.receiptNumber} (
              {selected.billNumber}) for “{reason.trim()}”. The original payment is kept on record.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setConfirming(false)} className="rounded border px-3 py-1.5 text-sm">
                Cancel
              </button>
              <button
                onClick={() => create.mutate()}
                disabled={create.isPending}
                className="rounded bg-red-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                Record refund
              </button>
            </div>
          </div>
        </div>
      )}
      {refundsQuery.isPending && <LoadingState />}
      {refundsQuery.isError && <ErrorState message="Could not load refunds." onRetry={() => void refundsQuery.refetch()} />}
      {refundsQuery.data && refundsQuery.data.data.length === 0 && (
        <EmptyState message="No refunds recorded yet." />
      )}
      {refundsQuery.data && refundsQuery.data.data.length > 0 && (
        <table className="w-full rounded-lg border border-stone-200 bg-white text-sm">
          <thead>
            <tr className="border-b border-stone-200 text-left text-stone-500">
              <th className="px-3 py-2">Reference</th>
              <th className="px-3 py-2">Bill</th>
              <th className="px-3 py-2">Receipt</th>
              <th className="px-3 py-2">Amount</th>
              <th className="px-3 py-2">Reason</th>
              <th className="px-3 py-2">By</th>
              <th className="px-3 py-2">At</th>
            </tr>
          </thead>
          <tbody>
            {refundsQuery.data.data.map((r) => (
              <tr key={r.id} className="border-b border-stone-100">
                <td className="px-3 py-2 font-medium">{r.refundReference}</td>
                <td className="px-3 py-2">{r.billNumber}</td>
                <td className="px-3 py-2">{r.receiptNumber}</td>
                <td className="px-3 py-2">{formatINR(r.amountPaise)}</td>
                <td className="px-3 py-2">{r.reason}</td>
                <td className="px-3 py-2">{r.refundedByName ?? "—"}</td>
                <td className="px-3 py-2">{new Date(r.refundedAt).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
