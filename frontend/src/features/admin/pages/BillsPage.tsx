/** Admin bills — Stage 7 § D + § H. History with a void action
 * (reason + confirmation). Voiding is only possible for unpaid
 * bills; a paid bill must be refunded instead (the server enforces
 * this — the UI shows the error). */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api/client";
import { fetchAdminBills, voidBill } from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/features/admin/components/AdminBits";
import { formatINR } from "@/shared/utils/money";

export function BillsPage() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [voiding, setVoiding] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["admin-bills", status, page],
    queryFn: () => fetchAdminBills({ status: status || undefined, page }),
    retry: false,
  });
  const voidMutation = useMutation({
    mutationFn: ({ billId, reason }: { billId: string; reason: string }) =>
      voidBill(billId, reason),
    onSuccess: () => {
      setMessage("Bill voided.");
      setVoiding(null);
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["admin-bills"] });
    },
    onError: (error) => {
      setMessage(
        error instanceof ApiError ? error.message : "Could not void the bill.",
      );
    },
  });

  return (
    <div>
      <PageHeader
        title="Bills"
        sub="Bill history — totals, collected, outstanding and refunds."
        actions={
          <select
            aria-label="Filter by status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
            className="rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          >
            <option value="">All statuses</option>
            {["issued", "payment_requested", "paid", "voided", "refunded", "partially_refunded"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        }
      />
      {message && (
        <p role="status" className="mb-3 rounded border border-stone-300 bg-white p-3 text-sm">
          {message}
        </p>
      )}
      {query.isPending && <LoadingState />}
      {query.isError && <ErrorState message="Could not load bills." onRetry={() => void query.refetch()} />}
      {query.data && query.data.data.length === 0 && <EmptyState message="No bills match this filter." />}
      {query.data && query.data.data.length > 0 && (
        <table className="w-full rounded-lg border border-stone-200 bg-white text-sm">
          <thead>
            <tr className="border-b border-stone-200 text-left text-stone-500">
              <th className="px-3 py-2">Bill</th>
              <th className="px-3 py-2">Table</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Total</th>
              <th className="px-3 py-2">Collected</th>
              <th className="px-3 py-2">Outstanding</th>
              <th className="px-3 py-2">Refunded</th>
              <th className="px-3 py-2">Issued</th>
              <th className="px-3 py-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {query.data.data.map((b) => (
              <tr key={b.id} className="border-b border-stone-100">
                <td className="px-3 py-2 font-medium">{b.billNumber}</td>
                <td className="px-3 py-2">{b.tableNumber}</td>
                <td className="px-3 py-2">{b.status}</td>
                <td className="px-3 py-2">{formatINR(b.totalPaise)}</td>
                <td className="px-3 py-2">{formatINR(b.collectedPaise)}</td>
                <td className="px-3 py-2">{formatINR(b.outstandingPaise)}</td>
                <td className="px-3 py-2">{formatINR(b.refundPaise)}</td>
                <td className="px-3 py-2">{new Date(b.issuedAt).toLocaleString()}</td>
                <td className="px-3 py-2">
                  {(b.status === "issued" || b.status === "payment_requested") && (
                    <button
                      onClick={() => setVoiding(b.id)}
                      className="rounded border border-red-300 px-2 py-1 text-xs text-red-700"
                    >
                      Void
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {voiding && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/40" role="dialog" aria-label="Void bill confirmation">
          <div className="w-full max-w-md rounded-lg bg-white p-5">
            <h2 className="text-lg font-semibold">Void this bill?</h2>
            <p className="mt-1 text-sm text-stone-600">
              The bill stays on record as voided and its items become billable again.
              Only unpaid bills can be voided — a paid bill must be refunded instead.
            </p>
            <label className="mt-4 block text-sm font-medium">
              Reason (required)
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="mt-1 w-full rounded border border-stone-300 px-2 py-1.5"
                placeholder="e.g. Guest left without ordering"
              />
            </label>
            {voidMutation.isError && (
              <p className="mt-2 text-sm text-red-700">
                {voidMutation.error instanceof ApiError
                  ? voidMutation.error.message
                  : "Could not void the bill."}
              </p>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => {
                  setVoiding(null);
                  setReason("");
                }}
                className="rounded border px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                disabled={!reason.trim() || voidMutation.isPending}
                onClick={() => voidMutation.mutate({ billId: voiding, reason: reason.trim() })}
                className="rounded bg-red-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                Void bill
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
