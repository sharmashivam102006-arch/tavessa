/** Guest connection indicator (Stage 8).
 *
 * Driven by the guest socket state from GuestLayout — it never
 * blocks the menu or any action. While the socket is down the pages
 * keep showing the last REST snapshot ("Reconnecting — showing last
 * saved state") and REST can still do everything; when the session
 * itself is gone the guest is told to reopen via QR instead of
 * watching a reconnect loop.
 */
import { useGuestSocketStatus } from "@/features/guest/hooks/useGuestSocket";
import { cn } from "@/lib/utils";

const LABELS = {
  live: "Live",
  connecting: "Connecting…",
  reconnecting: "Reconnecting…",
  offline: "Offline",
  "session-expired": "Session expired",
} as const;

export function GuestConnectionPill() {
  const { connection } = useGuestSocketStatus();
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium",
        connection === "live" && "bg-green-50 text-green-700",
        (connection === "connecting" || connection === "reconnecting") &&
          "bg-amber-50 text-amber-800",
        connection === "offline" && "bg-stone-100 text-stone-600",
        connection === "session-expired" && "bg-red-50 text-red-700",
      )}
      title={
        connection === "reconnecting"
          ? "Reconnecting — showing last saved state"
          : connection === "session-expired"
            ? "Session expired — reopen via QR"
            : undefined
      }
    >
      <span
        className={cn(
          "h-2 w-2 rounded-full",
          connection === "live" && "bg-green-500",
          (connection === "connecting" || connection === "reconnecting") &&
            "animate-pulse bg-amber-500",
          connection === "offline" && "bg-stone-400",
          connection === "session-expired" && "bg-red-500",
        )}
      />
      {LABELS[connection]}
    </span>
  );
}

/** One-line status for the tracking/help/bill pages. The
 * reconnecting/offline copy is the brief's wording: the data on
 * screen is the last saved state, not a live confirmation. */
export function GuestConnectionLine() {
  const { connection } = useGuestSocketStatus();
  if (connection === "live") {
    return (
      <p className="mt-1 flex items-center gap-2 text-sm text-green-700">
        <span className="h-2 w-2 rounded-full bg-green-500" />
        Live — updates arrive as they happen.
      </p>
    );
  }
  if (connection === "session-expired") {
    return (
      <p className="mt-1 text-sm text-red-700">
        Session expired — please reopen this table via its QR code to
        keep tracking live.
      </p>
    );
  }
  return (
    <p className="mt-1 flex items-center gap-2 text-sm text-amber-800">
      <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
      Reconnecting — showing last saved state.
    </p>
  );
}
