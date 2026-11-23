/** Elapsed time, ticking client-side every 10s.
 *
 * Derived from the server's `placedAt` — the client clock is never
 * business logic, only a display tick (re-rendered every 10s). No
 * SLA/breach flag: no threshold is configured (decision D7 open).
 */
import { useEffect, useState } from "react";

import { formatElapsed } from "@/features/kitchen/lib/kitchen";

export function ElapsedTime({ placedAt }: { placedAt: string }) {
  // `now` is state, updated by the interval — the displayed value is
  // always derived from placedAt + now, so a REST re-fetch never
  // needs a state reset and render stays pure.
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now());
    }, 10_000);
    return () => clearInterval(timer);
  }, []);

  const elapsedSeconds = Math.max(
    0,
    Math.floor((now - new Date(placedAt).getTime()) / 1000),
  );
  return (
    <span
      className="rounded-md bg-stone-100 px-2 py-1 text-sm font-semibold tabular-nums text-stone-800"
      title={`Placed ${formatElapsed(elapsedSeconds)} ago`}
    >
      {formatElapsed(elapsedSeconds)}
    </span>
  );
}
