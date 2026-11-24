/** Admin order history — Stage 7 § D. Filter by status, paginated. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchAdminOrders } from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/features/admin/components/AdminBits";
import { formatINR } from "@/shared/utils/money";

export function OrdersPage() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["admin-orders", status, page],
    queryFn: () => fetchAdminOrders({ status: status || undefined, page }),
    retry: false,
  });

  return (
    <div>
      <PageHeader
        title="Orders"
        sub="Order history with status filter."
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
            {["placed", "accepted", "preparing", "ready", "served", "completed", "cancelled"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        }
      />
      {query.isPending && <LoadingState />}
      {query.isError && <ErrorState message="Could not load orders." onRetry={() => void query.refetch()} />}
      {query.data && query.data.data.length === 0 && <EmptyState message="No orders match this filter." />}
      {query.data && query.data.data.length > 0 && (
        <>
          <table className="w-full rounded-lg border border-stone-200 bg-white text-sm">
            <thead>
              <tr className="border-b border-stone-200 text-left text-stone-500">
                <th className="px-3 py-2">Order</th>
                <th className="px-3 py-2">Table</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Items</th>
                <th className="px-3 py-2">Total</th>
                <th className="px-3 py-2">Placed</th>
              </tr>
            </thead>
            <tbody>
              {query.data.data.map((o) => (
                <tr key={o.id} className="border-b border-stone-100">
                  <td className="px-3 py-2 font-medium">{o.orderNumber}</td>
                  <td className="px-3 py-2">{o.tableNumber}</td>
                  <td className="px-3 py-2 capitalize">{o.status}</td>
                  <td className="px-3 py-2">{o.itemCount}</td>
                  <td className="px-3 py-2">{formatINR(o.totalPaise)}</td>
                  <td className="px-3 py-2">{new Date(o.placedAt).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 flex gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
              className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            >
              Previous
            </button>
            <button
              disabled={page >= query.data.pagination.total_pages}
              onClick={() => setPage(page + 1)}
              className="rounded border px-3 py-1 text-sm disabled:opacity-50"
            >
              Next
            </button>
            <span className="py-1 text-sm text-stone-500">
              Page {page} of {query.data.pagination.total_pages} ({query.data.pagination.total} orders)
            </span>
          </div>
        </>
      )}
    </div>
  );
}
