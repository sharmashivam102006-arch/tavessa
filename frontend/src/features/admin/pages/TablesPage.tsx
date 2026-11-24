/** Admin tables — Stage 7 § F. List + add (number unique per
 * branch). No delete in this stage. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api/client";
import { createTable, fetchTables } from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/features/admin/components/AdminBits";

export function TablesPage() {
  const queryClient = useQueryClient();
  const [number, setNumber] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["admin-tables"],
    queryFn: fetchTables,
    retry: false,
  });
  const create = useMutation({
    mutationFn: (n: number) => createTable(n),
    onSuccess: (table) => {
      setMessage(`Table ${table.number} added.`);
      setNumber("");
      void queryClient.invalidateQueries({ queryKey: ["admin-tables"] });
    },
    onError: (error) =>
      setMessage(error instanceof ApiError ? error.message : "Could not add the table."),
  });

  const tables = query.data ?? [];

  return (
    <div>
      <PageHeader title="Tables" sub="Table configuration for the restaurant." />
      {message && (
        <p role="status" className="mb-3 rounded border border-stone-300 bg-white p-3 text-sm">
          {message}
        </p>
      )}
      <form
        className="mb-6 flex gap-2 rounded-lg border border-stone-200 bg-white p-4"
        onSubmit={(e) => {
          e.preventDefault();
          const n = Number(number);
          if (Number.isInteger(n) && n >= 1) create.mutate(n);
        }}
      >
        <input
          aria-label="New table number"
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          placeholder="Table number"
          inputMode="numeric"
          className="rounded border border-stone-300 px-2 py-1.5 text-sm"
        />
        <button className="rounded bg-stone-900 px-3 py-1.5 text-sm text-white">Add table</button>
      </form>
      {query.isPending && <LoadingState />}
      {query.isError && <ErrorState message="Could not load tables." onRetry={() => void query.refetch()} />}
      {query.data && tables.length === 0 && <EmptyState message="No tables configured." />}
      {tables.length > 0 && (
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {tables.map((t) => (
            <div key={t.id} className="rounded-lg border border-stone-200 bg-white p-4 text-center">
              <p className="text-lg font-bold">Table {t.number}</p>
              <p className="text-xs capitalize text-stone-500">{t.status}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
