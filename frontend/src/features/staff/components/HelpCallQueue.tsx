/** The help-call queue — unresolved calls, oldest first (Stage 5).
 *
 * Each call shows the table, the type label, the guest's note and
 * how long it has waited. The flow is two-step by design: an open
 * call offers "Acknowledge"; an acknowledged call offers "Resolve".
 * Resolving an open call is not possible (the backend 409s it too).
 * Each call owns its own mutation instance, so a failed
 * acknowledge/resolve shows its error on that call and the call
 * stays put. In demo mode the buttons are disabled.
 */
import { ApiError } from "@/shared/api/client";
import { HELP_CALL_LABELS } from "@/shared/api/types";
import type { StaffHelpCall } from "@/shared/api/types";
import {
  useAcknowledgeHelpCall,
  useResolveHelpCall,
} from "@/features/staff/hooks/useStaff";
import { formatElapsed } from "@/features/kitchen/lib/kitchen";

function HelpCallCard({
  call,
  restaurantId,
  demo = false,
}: {
  call: StaffHelpCall;
  restaurantId: string;
  demo?: boolean;
}) {
  const acknowledge = useAcknowledgeHelpCall(restaurantId);
  const resolve = useResolveHelpCall(restaurantId);
  const pending = acknowledge.isPending || resolve.isPending;
  const error = acknowledge.error ?? resolve.error;
  const waitedSeconds = Math.max(
    0,
    Math.floor((Date.now() - new Date(call.createdAt).getTime()) / 1000),
  );

  return (
    <article
      className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm"
      aria-label={`Help call, table ${call.tableNumber}, ${HELP_CALL_LABELS[call.type ?? "waiter"]}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-lg font-bold">Table {call.tableNumber}</p>
          <p className="mt-0.5 text-base font-semibold text-stone-700">
            {HELP_CALL_LABELS[call.type ?? "waiter"]}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <span
            className="rounded-md bg-stone-100 px-2 py-1 text-sm font-semibold tabular-nums text-stone-800"
            title={`Waiting ${formatElapsed(waitedSeconds)}`}
          >
            {formatElapsed(waitedSeconds)}
          </span>
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
              call.status === "acknowledged"
                ? "bg-sky-100 text-sky-900"
                : "bg-red-100 text-red-900"
            }`}
          >
            {call.status === "acknowledged" ? "Acknowledged" : "Open"}
          </span>
          {demo ? (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900">
              DEMO
            </span>
          ) : null}
        </div>
      </div>
      {call.note ? (
        <p className="mt-2 rounded-md bg-amber-100 px-2.5 py-1.5 text-sm font-medium text-amber-900">
          {call.note}
        </p>
      ) : null}
      {error ? (
        <p
          className="mt-3 rounded-md bg-red-50 px-2.5 py-2 text-sm font-medium text-red-800"
          role="alert"
        >
          {error instanceof ApiError
            ? error.message
            : "Could not update this help call. It has not moved."}
        </p>
      ) : null}
      {demo ? (
        <p className="mt-3 rounded-lg bg-stone-100 px-4 py-2.5 text-center text-sm font-semibold text-stone-600">
          Acknowledge / Resolve — disabled in demo
        </p>
      ) : call.status === "open" ? (
        <button
          type="button"
          disabled={pending}
          onClick={() => acknowledge.mutate(call.id)}
          className="mt-3 w-full rounded-lg bg-brand-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-800 disabled:cursor-wait disabled:opacity-60"
        >
          {acknowledge.isPending ? "Acknowledging…" : "Acknowledge"}
        </button>
      ) : (
        <button
          type="button"
          disabled={pending}
          onClick={() => resolve.mutate(call.id)}
          className="mt-3 w-full rounded-lg bg-green-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-green-800 disabled:cursor-wait disabled:opacity-60"
        >
          {resolve.isPending ? "Resolving…" : "Resolve"}
        </button>
      )}
    </article>
  );
}

export function HelpCallQueue({
  calls,
  restaurantId,
  demo = false,
}: {
  calls: StaffHelpCall[];
  restaurantId: string;
  demo?: boolean;
}) {
  return (
    <section aria-label="Help calls">
      <h2 className="text-lg font-semibold">
        Help calls{" "}
        <span className="text-sm font-medium text-stone-500">
          ({calls.length} unresolved)
        </span>
      </h2>
      {calls.length === 0 ? (
        <p className="mt-3 rounded-xl border border-stone-200 bg-white p-6 text-sm text-stone-600">
          No open help calls — guest requests for a waiter, water or
          plates will appear here.
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          {calls.map((call) => (
            <HelpCallCard
              key={call.id}
              call={call}
              restaurantId={restaurantId}
              demo={demo}
            />
          ))}
        </div>
      )}
    </section>
  );
}
