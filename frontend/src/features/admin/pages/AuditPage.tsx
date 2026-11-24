/** Audit log — Stage 7 § I. Read-only list with action filter.
 * There is deliberately no edit or delete action anywhere here. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchAuditLogs } from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/features/admin/components/AdminBits";

export function AuditPage() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["admin-audit-logs", action, page],
    queryFn: () => fetchAuditLogs({ action: action || undefined, page }),
    retry: false,
  });

  return (
    <div>
      <PageHeader
        title="Audit log"
        sub="Append-only record of sensitive actions. Audit rows can never be edited or deleted."
        actions={
          <input
            aria-label="Filter by action"
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setPage(1);
            }}
            placeholder="Filter by action, e.g. refund.created"
            className="rounded border border-stone-300 bg-white px-2 py-1.5 text-sm"
          />
        }
      />
      {query.isPending && <LoadingState />}
      {query.isError && <ErrorState message="Could not load the audit log." onRetry={() => void query.refetch()} />}
      {query.data && query.data.data.length === 0 && <EmptyState message="No audit entries match this filter." />}
      {query.data && query.data.data.length > 0 && (
        <>
          <table className="w-full rounded-lg border border-stone-200 bg-white text-sm">
            <thead>
              <tr className="border-b border-stone-200 text-left text-stone-500">
                <th className="px-3 py-2">When</th>
                <th className="px-3 py-2">Action</th>
                <th className="px-3 py-2">Entity</th>
                <th className="px-3 py-2">Details</th>
              </tr>
            </thead>
            <tbody>
              {query.data.data.map((row) => (
                <tr key={row.id} className="border-b border-stone-100 align-top">
                  <td className="px-3 py-2 whitespace-nowrap">{new Date(row.createdAt).toLocaleString()}</td>
                  <td className="px-3 py-2 font-mono text-xs">{row.action}</td>
                  <td className="px-3 py-2 text-xs">
                    {row.entityType ?? "—"}
                    {row.entityId ? <span className="block text-stone-500">{row.entityId.slice(0, 8)}…</span> : null}
                  </td>
                  <td className="px-3 py-2 text-xs text-stone-600">
                    {row.details ? JSON.stringify(row.details) : "—"}
                  </td>
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
              Page {page} of {query.data.pagination.total_pages} ({query.data.pagination.total} entries)
            </span>
          </div>
        </>
      )}
    </div>
  );
}
