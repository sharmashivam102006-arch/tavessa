import { CircleAlert, LoaderCircle, PlugZap, SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center" role="status">
      <LoaderCircle className="h-8 w-8 animate-spin text-brand-700" />
      <p className="text-sm text-stone-600">{label}</p>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon?: ReactNode;
  title: string;
  body?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-stone-300 bg-white px-6 py-12 text-center">
      {icon ?? <SearchX className="h-8 w-8 text-stone-400" />}
      <h2 className="text-base font-semibold">{title}</h2>
      {body ? <p className="max-w-sm text-sm text-stone-600">{body}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  title,
  body,
  onRetry,
}: {
  title: string;
  body?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-6 py-10 text-center">
      <CircleAlert className="h-8 w-8 text-red-600" />
      <h2 className="text-base font-semibold text-red-900">{title}</h2>
      {body ? <p className="max-w-sm text-sm text-red-800">{body}</p> : null}
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

/**
 * Honest "backend not connected yet" state. Used by every write
 * action (order, help call, bill) and every read that must not be
 * invented (orders, bills). Names the exact endpoint and the stage
 * that adds it, and never claims the action succeeded.
 */
export function BackendNotConnected({
  endpoint,
  stage,
  what,
  children,
}: {
  endpoint: string;
  stage: string;
  what: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 px-5 py-6 text-left">
      <div className="flex items-start gap-3">
        <PlugZap className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
        <div>
          <h2 className="font-semibold text-amber-900">Not connected to the backend yet</h2>
          <p className="mt-1 text-sm text-amber-800">
            {what} needs <code className="rounded bg-amber-100 px-1 py-0.5 text-xs">{endpoint}</code>,
            which is not implemented yet — it arrives in {stage}. Nothing
            has been sent, and nothing below is a confirmed order, request
            or payment.
          </p>
          {children ? <div className="mt-3">{children}</div> : null}
        </div>
      </div>
    </div>
  );
}
