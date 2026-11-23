/** Connection-status pill (header). Driven by the kitchen socket
 * hook — Live / Connecting / Reconnecting / Offline. */
import type { KitchenConnectionStatus } from "@/shared/api/types";

const STYLES: Record<KitchenConnectionStatus, { label: string; className: string; dot: string }> = {
  live: {
    label: "Live",
    className: "bg-green-100 text-green-900",
    dot: "bg-green-600",
  },
  connecting: {
    label: "Connecting…",
    className: "bg-stone-200 text-stone-700",
    dot: "bg-stone-500",
  },
  reconnecting: {
    label: "Reconnecting…",
    className: "bg-amber-100 text-amber-900",
    dot: "bg-amber-600",
  },
  offline: {
    label: "Offline",
    className: "bg-red-100 text-red-900",
    dot: "bg-red-600",
  },
};

export function ConnectionStatus({ status }: { status: KitchenConnectionStatus }) {
  const style = STYLES[status];
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ${style.className}`}
      role="status"
    >
      <span className={`h-2 w-2 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  );
}
