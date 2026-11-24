/** Daily cash report + payments/refunds lists — Stage 7 § D + § H.
 * Gross collections, refunds, net and outstanding are always
 * shown separately and never double-counted. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  fetchAdminPayments,
  fetchAdminRefunds,
  fetchDailyCash,
  fetchDailyCashCsv,
} from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatCard,
} from "@/features/admin/components/AdminBits";
import { formatINR } from "@/shared/utils/money";

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export function CashPage() {
  const [date, setDate] = useState(todayISO());
  const reportQuery = useQuery({
    queryKey: ["admin-daily-cash", date],
    queryFn: () => fetchDailyCash(date),
    retry: false,
  });
  const paymentsQuery = useQuery({
    queryKey: ["admin-payments"],
    queryFn: () => fetchAdminPayments(1),
    retry: false,
  });
  const refundsQuery = useQuery({
    queryKey: ["admin-refunds"],
    queryFn: () => fetchAdminRefunds(1),
    retry: false,
  });

  const downloadCsv = async () => {
    const csv = await fetchDailyCashCsv(date);
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `daily-cash-${date}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <PageHeader
        title="Cash reports"
        sub="Daily cash reconciliation — gross, refunds, net, outstanding."
        actions={
          <div className="flex items-center gap-2">
            <input
              type="date"
              aria-label="Report date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="rounded border border-stone-300 px-2 py-1.5 text-sm"
            />
            <button onClick={() => void downloadCsv()} className="rounded border px-3 py-1.5 text-sm">
              Download CSV
            </button>
          </div>
        }
      />
      {reportQuery.isPending && <LoadingState />}
      {reportQuery.isError && (
        <ErrorState message="Could not load the daily cash report." onRetry={() => void reportQuery.refetch()} />
      )}
      {reportQuery.data && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard label="Gross cash collected" value={formatINR(reportQuery.data.grossCashCollectedPaise)} sub={`${reportQuery.data.paymentCount} payments`} />
          <StatCard label="Cash refunds" value={formatINR(reportQuery.data.cashRefundsPaise)} sub={`${reportQuery.data.refundCount} refunds`} />
          <StatCard label="Net cash" value={formatINR(reportQuery.data.netCashPaise)} sub="Gross − refunds" />
          <StatCard label="Outstanding" value={formatINR(reportQuery.data.outstandingPaise)} sub={`${reportQuery.data.billCount} bills issued`} />
        </div>
      )}
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <section className="rounded-lg border border-stone-200 bg-white p-4">
          <h2 className="font-semibold">Payments</h2>
          {paymentsQuery.isPending && <LoadingState />}
          {paymentsQuery.data && paymentsQuery.data.data.length === 0 && (
            <EmptyState message="No payments recorded yet." />
          )}
          <ul className="mt-2 divide-y divide-stone-100">
            {(paymentsQuery.data?.data ?? []).map((p) => (
              <li key={p.id} className="flex justify-between py-2 text-sm">
                <span>
                  {p.receiptNumber} · {p.billNumber} · {p.status}
                  {p.collectedByName ? ` · ${p.collectedByName}` : ""}
                </span>
                <span className="font-medium">{formatINR(p.amountPaise)}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="rounded-lg border border-stone-200 bg-white p-4">
          <h2 className="font-semibold">Refunds</h2>
          {refundsQuery.isPending && <LoadingState />}
          {refundsQuery.data && refundsQuery.data.data.length === 0 && (
            <EmptyState message="No refunds recorded yet." />
          )}
          <ul className="mt-2 divide-y divide-stone-100">
            {(refundsQuery.data?.data ?? []).map((r) => (
              <li key={r.id} className="flex justify-between py-2 text-sm">
                <span>
                  {r.refundReference} · {r.billNumber} · {r.reason}
                </span>
                <span className="font-medium">{formatINR(r.amountPaise)}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
