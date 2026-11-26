/** TanStack Query hooks + the kitchen WebSocket hook (Stage 4).
 *
 * REST is the source of truth. The WebSocket only announces change:
 * on (re)connect and on any order event the `kitchen-orders` query
 * is invalidated and re-fetched — WS data is never applied as state
 * directly (api-plan.md § 9.4, architecture.md § 6.4). A failed
 * socket therefore never breaks the board: it keeps showing the
 * last REST snapshot, with a Refresh button and a reconnecting
 * indicator.
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchKitchenOrders,
  fetchKitchenRestaurants,
  getKitchenDevKey,
  isKitchenConfigured,
  patchKitchenOrderStatus,
} from "@/features/kitchen/api/kitchenApi";
import { fetchWsTicket } from "@/features/admin/api/adminApi";
import {
  buildKitchenWsUrl,
  nextBackoffDelayMs,
} from "@/features/kitchen/lib/kitchen";
import type {
  KitchenConnectionStatus,
  KitchenEvent,
} from "@/shared/api/types";

const API_BASE = import.meta.env.VITE_API_URL ?? "";
/** Optional direct-to-Render WebSocket base (production:
 * wss://tavessa-api.onrender.com). Vite env vars are embedded in
 * the frontend bundle at build time — this is public configuration
 * (a hostname), never a secret. When unset, sockets use the
 * existing same-origin fallback (local dev). */
const WS_BASE = import.meta.env.VITE_WS_URL ?? "";

export function useKitchenRestaurants() {
  return useQuery({
    queryKey: ["kitchen-restaurants"],
    queryFn: fetchKitchenRestaurants,
    retry: false,
    staleTime: 60_000,
    enabled: isKitchenConfigured(),
  });
}

/** The active queue for one restaurant. Key ["kitchen-orders",
 * restaurantId]; staleTime ~10s. Columns are derived client-side
 * from the full active list (see columnForStatus). */
export function useKitchenOrders(restaurantId: string | undefined) {
  return useQuery({
    queryKey: ["kitchen-orders", restaurantId],
    queryFn: () => fetchKitchenOrders(restaurantId as string),
    retry: false,
    staleTime: 10_000,
    enabled: Boolean(restaurantId) && isKitchenConfigured(),
  });
}

/** One status transition. Each ticket owns its own mutation instance,
 * so `isPending` / `error` are per-ticket: a failed transition shows
 * its error on that ticket and the ticket stays put. On success the
 * queue is invalidated and the ticket moves only after the server
 * confirms. */
export function useTransitionOrder(restaurantId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      orderId,
      status,
    }: {
      orderId: string;
      status: "preparing" | "ready";
    }) => patchKitchenOrderStatus(orderId, restaurantId, status),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["kitchen-orders", restaurantId],
      });
    },
  });
}

export interface KitchenSocketOptions {
  onEvent?: (event: KitchenEvent) => void;
}

/** Native WebSocket to /ws/kitchen with exponential-backoff
 * reconnect (1s, 2s, 4s, … max 30s). Returns the connection status
 * that drives the header pill. On (re)connect and on every event
 * the kitchen-orders query is invalidated (REST re-fetch). */
export function useKitchenSocket(
  restaurantId: string | undefined,
  options?: KitchenSocketOptions,
): { connection: KitchenConnectionStatus } {
  const queryClient = useQueryClient();
  const [connection, setConnection] =
    useState<KitchenConnectionStatus>("offline");
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onEventRef = useRef(options?.onEvent);
  useEffect(() => {
    onEventRef.current = options?.onEvent;
  });
  // Without a restaurant or auth (dev key in DEV, JWT in
  // production) there is no socket to open; the reported status
  // is offline without an effect setState.
  const disabled = !restaurantId || !isKitchenConfigured();

  useEffect(() => {
    if (!restaurantId || !isKitchenConfigured()) {
      return undefined;
    }
    let socket: WebSocket | undefined;
    let closed = false;

    const invalidate = () => {
      void queryClient.invalidateQueries({
        queryKey: ["kitchen-orders", restaurantId],
      });
    };

    const connect = async () => {
      if (closed) return;
      setConnection(attemptRef.current === 0 ? "connecting" : "reconnecting");
      // A ticket is single-use, so every (re)connect fetches a
      // fresh one in production; in DEV the dev key is used.
      let url: string;
      if (import.meta.env.DEV) {
        const devKey = getKitchenDevKey();
        if (!devKey) return;
        url = buildKitchenWsUrl({
          apiBase: API_BASE,
          wsBase: WS_BASE,
          pageOrigin: window.location.origin,
          restaurantId,
          devKey,
        });
      } else {
        try {
          const ticket = await fetchWsTicket("kitchen");
          if (closed) return;
          url = buildKitchenWsUrl({
            apiBase: API_BASE,
            wsBase: WS_BASE,
            pageOrigin: window.location.origin,
            restaurantId,
            ticket: ticket.ticket,
          });
        } catch {
          if (closed) return;
          const delay = nextBackoffDelayMs(attemptRef.current);
          attemptRef.current += 1;
          setConnection("reconnecting");
          timerRef.current = setTimeout(() => void connect(), delay);
          return;
        }
      }
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
          const event = JSON.parse(message.data as string) as KitchenEvent;
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
        timerRef.current = setTimeout(() => void connect(), delay);
      };
      socket.onerror = () => {
        // onclose follows and schedules the reconnect.
        socket?.close();
      };
    };

    attemptRef.current = 0;
    void connect();
    return () => {
      closed = true;
      if (timerRef.current !== undefined) clearTimeout(timerRef.current);
      socket?.close();
    };
  }, [restaurantId, queryClient]);

  return { connection: disabled ? "offline" : connection };
}
