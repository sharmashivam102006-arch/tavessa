/** Owner overview — Stage 7 § D. Metrics + date presets, orders
 * by status, recent payments, recent activity, best sellers.
 * Gross sales, cash collected, refunds, net cash and outstanding
 * are shown as SEPARATE figures: an unpaid bill is never presented
 * as cash collected. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchDashboard } from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatCard,
} from "@/features/admin/components/AdminBits";
import { formatINR } from "@/shared/utils/money";

const PRESETS = ["today", "yesterday", "last7", "last30"] as const;

export function OverviewPage() {
  const [preset, setPreset] = useState<string>("today");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const query = useQuery({
    queryKey: ["admin-overview", preset, from, to],
    queryFn: () =>
      fetchDashboard(
        preset,
        preset === "custom" ? from : undefined,
        preset === "custom" ? to : undefined,
      ),
    retry: false,
  });

  return (
    <div>
      <PageHeader
        title="Overview"
        sub="Live operations, sales and cash for the restaurant."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {PRESETS.map((p) => (
              <button
                key={p}
                onClick={() => setPreset(p)}
                className={`rounded border px-3 py-1.5 text-sm ${
                  preset === p ? "border-stone-900 bg-stone-900 text-white" : "border-stone-300 bg-white"
                }`}
              >
                {p === "last7" ? "Last 7 days" : p === "last30" ? "Last 30 days" : p[0].toUpperCase() + p.slice(1)}
              </button>
            ))}
            <button
              onClick={() => setPreset("custom")}
              className={`rounded border px-3 py-1.5 text-sm ${
                preset === "custom" ? "border-stone-900 bg-stone-900 text-white" : "border-stone-300 bg-white"
              }`}
            >
              Custom
            </button>
            {preset === "custom" && (
              <>
                <input
                  type="date"
                  aria-label="From date"
                  value={from}
                  onChange={(e) => setFrom(e.target.value)}
                  className="rounded border border-stone-300 px-2 py-1.5 text-sm"
                />
                <input
                  type="date"
                  aria-label="To date"
                  value={to}
                  onChange={(e) => setTo(e.target.value)}
                  className="rounded border border-stone-300 px-2 py-1.5 text-sm"
                />
              </>
            )}
          </div>
        }
      />
      {query.isPending && <LoadingState label="Loading overview…" />}
      {query.isError && (
        <ErrorState message="Could not load the overview." onRetry={() => void query.refetch()} />
      )}
      {query.data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatCard label="Gross sales" value={formatINR(query.data.grossSalesPaise)} sub={`${query.data.billCount} bills · unpaid bills included`} />
            <StatCard label="Cash collected" value={formatINR(query.data.cashCollectedPaise)} sub="Confirmed cash payments in period" />
            <StatCard label="Refunds" value={formatINR(query.data.refundsPaise)} sub="Cash refunded in period" />
            <StatCard label="Net cash" value={formatINR(query.data.netCashPaise)} sub="Collected − refunds" />
            <StatCard label="Outstanding" value={formatINR(query.data.outstandingPaise)} sub="Open bills, as of now" />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <section className="rounded-lg border border-stone-200 bg-white p-4">
              <h2 className="font-semibold">Orders by status</h2>
              {query.data.ordersByStatus.length === 0 ? (
                <EmptyState message="No orders in this period." />
              ) : (
                <ul className="mt-2 divide-y divide-stone-100">
                  {query.data.ordersByStatus.map((row) => (
                    <li key={row.status} className="flex justify-between py-2 text-sm">
                      <span className="capitalize">{row.status}</span>
                      <span className="font-medium">{row.count}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 text-sm text-stone-600">
                Average order value: {formatINR(query.data.averageOrderValuePaise)} ·{" "}
                {query.data.orderCount} orders
              </p>
            </section>
            <section className="rounded-lg border border-stone-200 bg-white p-4">
              <h2 className="font-semibold">Best sellers</h2>
              {query.data.bestSellers.length === 0 ? (
                <EmptyState message="No items sold in this period." />
              ) : (
                <ul className="mt-2 divide-y divide-stone-100">
                  {query.data.bestSellers.map((item) => (
                    <li key={item.menuItemId} className="flex justify-between py-2 text-sm">
                      <span>{item.name}</span>
                      <span>
                        {item.quantity} × · {formatINR(item.revenuePaise)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="rounded-lg border border-stone-200 bg-white p-4">
              <h2 className="font-semibold">Recent payments</h2>
              {query.data.recentPayments.length === 0 ? (
                <EmptyState message="No payments in this period." />
              ) : (
                <ul className="mt-2 divide-y divide-stone-100">
                  {query.data.recentPayments.map((p) => (
                    <li key={p.id} className="flex justify-between py-2 text-sm">
                      <span>
                        {p.billNumber} · Table {p.tableNumber} · {p.receiptNumber}
                      </span>
                      <span className="font-medium">{formatINR(p.amountPaise)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="rounded-lg border border-stone-200 bg-white p-4">
              <h2 className="font-semibold">Recent activity</h2>
              {query.data.recentActivity.length === 0 ? (
                <EmptyState message="No activity recorded yet." />
              ) : (
                <ul className="mt-2 divide-y divide-stone-100">
                  {query.data.recentActivity.map((a) => (
                    <li key={a.id} className="py-2 text-sm">
                      <span className="font-mono text-xs">{a.action}</span>
                      <span className="block text-xs text-stone-500">
                        {new Date(a.createdAt).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
