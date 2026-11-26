/** Guest WebSocket hook (Stage 8) + the connection context the
 * guest pages read.
 *
 * ONE socket per table session, owned by GuestLayout (the layout
 * wraps every guest page, so the socket is opened once and shared).
 * Pages read the state through `useGuestSocketStatus()` — they must
 * never call `useGuestSocket` themselves, or a second socket would
 * open for the same session.
 *
 * REST is the source of truth. The socket only announces change: on
 * (re)connect and on any guest event the guest query keys are
 * invalidated and re-fetched — WS data is never applied as state
 * directly (api-plan.md § 9.4, architecture.md § 6.4). A failed
 * socket therefore never breaks a page: it keeps showing the last
 * REST snapshot, with a "Reconnecting — showing last saved state"
 * indicator, and REST can still do everything (ordering, help,
 * bill actions).
 *
 * - Ticket: a fresh single-use ticket is fetched for EVERY
 *   (re)connect (POST /guest/ws/tickets, X-Table-Token header).
 *   No ticket or table token is ever stored (no localStorage) or
 *   logged.
 * - Backoff: exponential WITH jitter, 1s → 30s max
 *   (guestSocket.ts). On ticket expiry/consumption a FRESH ticket
 *   is fetched and the socket reconnects.
 * - Version: the server's per-session version counter is
 *   monotonic; an event with a version at or below the highest
 *   seen is ignored (stale/out-of-order). This is NOT an
 *   event-replay system — a gap just triggers the REST refetch.
 * - Session expiry: after 3 consecutive failures that say the
 *   session is gone (ticket fetch 401/410, or a 4401/4404 close),
 *   the hook STOPS and reports `session-expired` — "Session
 *   expired — reopen via QR" — instead of looping forever. A
 *   network failure is not a session failure: it just backs off.
 * - While the socket is down, a slow background refetch (30s) and
 *   a refetch on window focus keep the pages from going stale.
 *   While it is live there is no polling at all.
 */
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api/client";
import type {
  GuestConnectionStatus,
  GuestEvent,
} from "@/shared/api/types";
import { fetchGuestWsTicket } from "@/features/guest/api/guestApi";
import { isDemoToken } from "@/features/guest/hooks/useGuest";
import {
  buildGuestWsUrl,
  nextGuestBackoffDelayMs,
} from "@/features/guest/lib/guestSocket";

const API_BASE = import.meta.env.VITE_API_URL ?? "";
/** Optional direct-to-Render WebSocket base (production:
 * wss://tavessa-api.onrender.com). Vite env vars are embedded in
 * the frontend bundle at build time — this is public configuration
 * (a hostname), never a secret. When unset, sockets use the
 * existing same-origin fallback (local dev). */
const WS_BASE = import.meta.env.VITE_WS_URL ?? "";

/** Failures in a row that say the session is gone before giving up. */
const MAX_SESSION_FAILURES = 3;
/** Slow background refetch while the socket is down (ms). */
const OFFLINE_REFETCH_MS = 30_000;

export interface GuestSocketOptions {
  onEvent?: (event: GuestEvent) => void;
}

export interface GuestSocketState {
  connection: GuestConnectionStatus;
  /** The most recent guest event, for pages that react to one
   * specific event (e.g. BillPage's voided-bill banner). REST is
   * still authoritative — this is a hint, not state. */
  lastEvent: GuestEvent | null;
}

const GuestSocketContext = createContext<GuestSocketState | null>(null);

/** Read the guest socket state from GuestLayout's provider. Outside
 * a guest layout (e.g. a page rendered standalone in a test) the
 * socket is simply offline — no throw, no socket. */
export function useGuestSocketStatus(): GuestSocketState {
  return (
    useContext(GuestSocketContext) ?? {
      connection: "offline",
      lastEvent: null,
    }
  );
}

export function GuestSocketProvider({
  value,
  children,
}: {
  value: GuestSocketState;
  children: ReactNode;
}) {
  return (
    <GuestSocketContext.Provider value={value}>
      {children}
    </GuestSocketContext.Provider>
  );
}

function guestQueryKeys(tableToken: string) {
  return [
    ["guest-orders", tableToken],
    ["guest-order", tableToken],
    ["guest-help-calls", tableToken],
    ["guest-bill", tableToken],
    ["guest-receipt", tableToken],
  ] as const;
}

export function useGuestSocket(
  tableToken: string,
  options?: GuestSocketOptions,
): GuestSocketState {
  const queryClient = useQueryClient();
  const [connection, setConnection] =
    useState<GuestConnectionStatus>("offline");
  const [lastEvent, setLastEvent] = useState<GuestEvent | null>(null);
  const attemptRef = useRef(0);
  const sessionFailuresRef = useRef(0);
  const lastVersionRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onEventRef = useRef(options?.onEvent);
  useEffect(() => {
    onEventRef.current = options?.onEvent;
  });
  // Mirrors `connection` for the interval/focus handlers below,
  // which must only refetch while the socket is actually down.
  const connectionRef = useRef(connection);
  useEffect(() => {
    connectionRef.current = connection;
  }, [connection]);

  // The demo preview has no backend session, so there is no socket
  // to open; the reported status is offline without an effect
  // setState.
  const disabled = !tableToken || isDemoToken(tableToken);

  useEffect(() => {
    if (!tableToken || isDemoToken(tableToken)) {
      return undefined;
    }
    let socket: WebSocket | undefined;
    let closed = false;

    const invalidateGuestQueries = () => {
      for (const key of guestQueryKeys(tableToken)) {
        void queryClient.invalidateQueries({ queryKey: [...key] });
      }
    };

    const stopAsSessionExpired = () => {
      if (closed) return;
      setConnection("session-expired");
    };

    const scheduleReconnect = () => {
      if (closed) return;
      const delay = nextGuestBackoffDelayMs(attemptRef.current);
      attemptRef.current += 1;
      setConnection("reconnecting");
      timerRef.current = setTimeout(() => void connect(), delay);
    };

    /** A failure that says the SESSION is gone (not the network).
     * Counted; after MAX_SESSION_FAILURES in a row, stop. */
    const noteSessionFailure = () => {
      sessionFailuresRef.current += 1;
      if (sessionFailuresRef.current >= MAX_SESSION_FAILURES) {
        stopAsSessionExpired();
        return true;
      }
      return false;
    };

    const connect = async () => {
      if (closed) return;
      setConnection(attemptRef.current === 0 ? "connecting" : "reconnecting");
      let url: string;
      try {
        const { data } = await fetchGuestWsTicket(tableToken);
        if (closed) return;
        url = buildGuestWsUrl({
          apiBase: API_BASE,
          wsBase: WS_BASE,
          pageOrigin: window.location.origin,
          ticket: data.ticket,
        });
      } catch (err) {
        if (closed) return;
        if (err instanceof ApiError && (err.status === 401 || err.status === 410)) {
          // The table token/session is gone: count towards
          // session-expired; otherwise keep backing off.
          if (!noteSessionFailure()) scheduleReconnect();
        } else {
          scheduleReconnect();
        }
        return;
      }
      socket = new WebSocket(url);
      socket.onopen = () => {
        if (closed) return;
        attemptRef.current = 0;
        sessionFailuresRef.current = 0;
        setConnection("live");
        invalidateGuestQueries();
      };
      socket.onmessage = (message) => {
        if (closed) return;
        let event: GuestEvent;
        try {
          event = JSON.parse(message.data as string) as GuestEvent;
        } catch {
          // A malformed event is ignored — REST is the truth.
          return;
        }
        if (typeof event.version === "number") {
          if (event.version <= lastVersionRef.current) return;
          lastVersionRef.current = event.version;
        }
        setLastEvent(event);
        onEventRef.current?.(event);
        invalidateGuestQueries();
      };
      socket.onclose = (closeEvent) => {
        if (closed) return;
        const code = (closeEvent as CloseEvent).code;
        if (code === 4401 || code === 4404) {
          // Bad/expired/used ticket, or the session went away
          // while connected: count towards session-expired; a
          // fresh ticket + reconnect is the recovery until then.
          if (!noteSessionFailure()) scheduleReconnect();
          return;
        }
        scheduleReconnect();
      };
      socket.onerror = () => {
        // onclose follows and schedules the reconnect.
        socket?.close();
      };
    };

    attemptRef.current = 0;
    sessionFailuresRef.current = 0;
    lastVersionRef.current = 0;
    void connect();

    // While the socket is down, keep the pages honest with a slow
    // background refetch + a refetch on window focus. While it is
    // live there is no polling at all.
    const offlineTimer = setInterval(() => {
      if (closed || connectionRef.current === "live") return;
      invalidateGuestQueries();
    }, OFFLINE_REFETCH_MS);
    const onFocus = () => {
      if (closed || connectionRef.current === "live") return;
      invalidateGuestQueries();
    };
    window.addEventListener("focus", onFocus);
    return () => {
      closed = true;
      if (timerRef.current !== undefined) clearTimeout(timerRef.current);
      clearInterval(offlineTimer);
      window.removeEventListener("focus", onFocus);
      socket?.close();
    };
  }, [tableToken, queryClient]);

  return {
    connection: disabled ? "offline" : connection,
    lastEvent,
  };
}
