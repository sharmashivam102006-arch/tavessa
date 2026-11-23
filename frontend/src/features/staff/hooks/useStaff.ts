/** TanStack Query hooks + the staff WebSocket hook (Stage 5).
 *
 * REST is the source of truth. The WebSocket only announces change:
 * on (re)connect and on any staff event the staff queries are
 * invalidated and re-fetched — WS data is never applied as state
 * directly (api-plan.md § 9.4, architecture.md § 6.4). A failed
 * socket therefore never breaks the board: it keeps showing the
 * last REST snapshot, with a Refresh button and a reconnecting
 * indicator.
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  acknowledgeHelpCall,
  fetchStaffBill,
  fetchStaffBillPayments,
  fetchStaffBillReceipt,
  fetchStaffBills,
  fetchStaffHelpCalls,
  fetchStaffOrders,
  fetchStaffRestaurants,
  fetchStaffTableDetail,
  fetchStaffTables,
  getStaffDevKey,
  isStaffConfigured,
  recordStaffPayment,
  resolveHelpCall,
  serveStaffOrder,
} from "@/features/staff/api/staffApi";
import {
  buildStaffWsUrl,
  nextBackoffDelayMs,
} from "@/features/staff/lib/staff";
import type {
  StaffConnectionStatus,
  StaffEvent,
} from "@/shared/api/types";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

export function useStaffRestaurants() {
  return useQuery({
    queryKey: ["staff-restaurants"],
    queryFn: fetchStaffRestaurants,
    retry: false,
    staleTime: 60_000,
    enabled: isStaffConfigured(),
  });
}

/** The floor grid for one restaurant (number order). */
export function useStaffTables(restaurantId: string | undefined) {
  return useQuery({
    queryKey: ["staff-tables", restaurantId],
    queryFn: () => fetchStaffTables(restaurantId as string),
    retry: false,
    staleTime: 10_000,
    enabled: Boolean(restaurantId) && isStaffConfigured(),
  });
}

/** One table's detail: session + its orders + its open help calls. */
export function useStaffTableDetail(
  restaurantId: string | undefined,
  tableId: string | undefined,
) {
  return useQuery({
    queryKey: ["staff-table-detail", restaurantId, tableId],
    queryFn: () =>
      fetchStaffTableDetail(restaurantId as string, tableId as string),
    retry: false,
    staleTime: 10_000,
    enabled: Boolean(restaurantId) && Boolean(tableId) && isStaffConfigured(),
  });
}

/** Active orders, oldest first. Key ["staff-orders", restaurantId]. */
export function useStaffOrders(restaurantId: string | undefined) {
  return useQuery({
    queryKey: ["staff-orders", restaurantId],
    queryFn: () => fetchStaffOrders(restaurantId as string),
    retry: false,
    staleTime: 10_000,
    enabled: Boolean(restaurantId) && isStaffConfigured(),
  });
}

/** Unresolved help calls, oldest first. */
export function useStaffHelpCalls(restaurantId: string | undefined) {
  return useQuery({
    queryKey: ["staff-help-calls", restaurantId],
    queryFn: () => fetchStaffHelpCalls(restaurantId as string),
    retry: false,
    staleTime: 10_000,
    enabled: Boolean(restaurantId) && isStaffConfigured(),
  });
}

/** Serve one ready order. The order moves ONLY after the server
 * confirms (onSuccess invalidates the orders + tables queries); on
 * error the mutation error shows on the card and the order stays
 * put. */
export function useServeOrder(restaurantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (orderId: string) => serveStaffOrder(orderId, restaurantId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["staff-orders", restaurantId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-tables", restaurantId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-table-detail", restaurantId],
      });
    },
  });
}

function useInvalidateStaff(restaurantId: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({
      queryKey: ["staff-orders", restaurantId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["staff-tables", restaurantId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["staff-help-calls", restaurantId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["staff-table-detail", restaurantId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["staff-bills", restaurantId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["staff-bill", restaurantId],
    });
  };
}

/** Acknowledge one help call (open -> acknowledged). */
export function useAcknowledgeHelpCall(restaurantId: string) {
  const invalidate = useInvalidateStaff(restaurantId);
  return useMutation({
    mutationFn: (callId: string) => acknowledgeHelpCall(callId, restaurantId),
    onSuccess: invalidate,
  });
}

/** Resolve one help call (acknowledged -> resolved). */
export function useResolveHelpCall(restaurantId: string) {
  const invalidate = useInvalidateStaff(restaurantId);
  return useMutation({
    mutationFn: (callId: string) => resolveHelpCall(callId, restaurantId),
    onSuccess: invalidate,
  });
}

/* ------------------------------------------------------------------ */
/* Billing (Stage 6)                                                   */
/* ------------------------------------------------------------------ */

/** The bill queue for one restaurant (payment_requested first).
 * Key ["staff-bills", restaurantId, status]. */
export function useStaffBills(
  restaurantId: string | undefined,
  status?: "issued" | "payment_requested" | "paid",
) {
  return useQuery({
    queryKey: ["staff-bills", restaurantId, status ?? "all"],
    queryFn: () => fetchStaffBills(restaurantId as string, status),
    retry: false,
    staleTime: 10_000,
    enabled: Boolean(restaurantId) && isStaffConfigured(),
  });
}

/** One bill in full: items, splits, payment history, totals. */
export function useStaffBill(
  restaurantId: string | undefined,
  billId: string | undefined,
) {
  return useQuery({
    queryKey: ["staff-bill", restaurantId, billId],
    queryFn: () => fetchStaffBill(restaurantId as string, billId as string),
    retry: false,
    staleTime: 10_000,
    enabled:
      Boolean(restaurantId) && Boolean(billId) && isStaffConfigured(),
  });
}

/** Payment history for a bill, newest first. */
export function useStaffBillPayments(
  restaurantId: string | undefined,
  billId: string | undefined,
) {
  return useQuery({
    queryKey: ["staff-bill-payments", restaurantId, billId],
    queryFn: () =>
      fetchStaffBillPayments(restaurantId as string, billId as string),
    retry: false,
    staleTime: 10_000,
    enabled:
      Boolean(restaurantId) && Boolean(billId) && isStaffConfigured(),
  });
}

/** Receipt data for a paid bill. Only enabled once the bill is
 * paid — before that the endpoint 409s, so the hook stays idle. */
export function useStaffBillReceipt(
  restaurantId: string | undefined,
  billId: string | undefined,
  billPaid: boolean,
) {
  return useQuery({
    queryKey: ["staff-bill-receipt", restaurantId, billId],
    queryFn: () =>
      fetchStaffBillReceipt(restaurantId as string, billId as string),
    retry: false,
    staleTime: 60_000,
    enabled:
      Boolean(restaurantId) &&
      Boolean(billId) &&
      billPaid &&
      isStaffConfigured(),
  });
}

/** Confirm cash collection for a bill (or one split). The mutation
 * generates ONE idempotency key per confirmation attempt and reuses
 * it across retries of that attempt (the key lives in the closure,
 * so a double-tap cannot create two payments). On success the
 * bills + tables + table-detail queries are invalidated; on error
 * the mutation error shows on the panel and the bill stays in the
 * queue. */
export function useRecordPayment(restaurantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      billId: string;
      amountPaise: number;
      billSplitId?: string;
      note?: string;
    }) =>
      recordStaffPayment(restaurantId, args.billId, {
        amountPaise: args.amountPaise,
        billSplitId: args.billSplitId,
        note: args.note,
      }),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: ["staff-bills", restaurantId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-bill", restaurantId, variables.billId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-bill-payments", restaurantId, variables.billId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-bill-receipt", restaurantId, variables.billId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-tables", restaurantId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-table-detail", restaurantId],
      });
    },
  });
}

export interface StaffSocketOptions {
  onEvent?: (event: StaffEvent) => void;
}

/** Native WebSocket to /ws/staff with exponential-backoff
 * reconnect (1s, 2s, 4s, … max 30s). Returns the connection status
 * that drives the header pill. On (re)connect and on every event
 * the staff queries are invalidated (REST re-fetch). */
export function useStaffSocket(
  restaurantId: string | undefined,
  options?: StaffSocketOptions,
): { connection: StaffConnectionStatus } {
  const queryClient = useQueryClient();
  const [connection, setConnection] =
    useState<StaffConnectionStatus>("offline");
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onEventRef = useRef(options?.onEvent);
  useEffect(() => {
    onEventRef.current = options?.onEvent;
  });
  // Without a restaurant or a dev key there is no socket to open;
  // the reported status is offline without an effect setState.
  const disabled = !restaurantId || !getStaffDevKey();

  useEffect(() => {
    const devKey = getStaffDevKey();
    if (!restaurantId || !devKey) {
      return undefined;
    }
    let socket: WebSocket | undefined;
    let closed = false;

    const invalidate = () => {
      void queryClient.invalidateQueries({
        queryKey: ["staff-orders", restaurantId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-tables", restaurantId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-help-calls", restaurantId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-table-detail", restaurantId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-bills", restaurantId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["staff-bill", restaurantId],
      });
    };

    const connect = () => {
      if (closed) return;
      setConnection(attemptRef.current === 0 ? "connecting" : "reconnecting");
      const url = buildStaffWsUrl({
        apiBase: API_BASE,
        pageOrigin: window.location.origin,
        restaurantId,
        devKey,
      });
      socket = new WebSocket(url);
      socket.onopen = () => {
        if (closed) return;
        attemptRef.current = 0;
        setConnection("live");
        invalidate();
      };
      socket.onmessage = (message) => {
        if (closed) return;
        try {
          const event = JSON.parse(message.data as string) as StaffEvent;
          onEventRef.current?.(event);
        } catch {
          // A malformed event is ignored — REST is the truth.
        }
        invalidate();
      };
      socket.onclose = () => {
        if (closed) return;
        const delay = nextBackoffDelayMs(attemptRef.current);
        attemptRef.current += 1;
        setConnection("reconnecting");
        timerRef.current = setTimeout(connect, delay);
      };
      socket.onerror = () => {
        // onclose follows and schedules the reconnect.
        socket?.close();
      };
    };

    attemptRef.current = 0;
    connect();
    return () => {
      closed = true;
      if (timerRef.current !== undefined) clearTimeout(timerRef.current);
      socket?.close();
    };
  }, [restaurantId, queryClient]);

  return { connection: disabled ? "offline" : connection };
}
