/** TanStack Query hooks for the guest interface.
 *
 * The backend guest endpoints do not exist yet (Stage 3). Every hook
 * therefore has an explicit fallback contract, and the UI labels
 * sample data as sample data:
 * - useGuestSession: retry:false. On failure the UI renders the
 *   invalid/expired/not-connected state mapped from the error —
 *   never a fake verified session.
 * - useGuestMenu: tries GET /guest/menu; when the endpoint is
 *   unavailable it falls back to the clearly-marked Zaika SAMPLE
 *   dataset, and returns `isSample: true` so the UI can say so.
 * - useGuestOrders / useGuestOrder / useGuestBill: no fallback —
 *   orders and bills must never be invented. On failure the UI shows
 *   the not-connected state.
 */
import { useQuery } from "@tanstack/react-query";

import { ApiError, mapSessionError, type SessionErrorKind } from "@/shared/api/client";
import type { GuestMenuResponse } from "@/shared/api/types";
import {
  fetchGuestBill,
  fetchGuestMenu,
  fetchGuestOrder,
  fetchGuestOrders,
  fetchGuestSession,
} from "@/features/guest/api/guestApi";
import { ZAIKA_CATEGORIES, ZAIKA_MENU_ITEMS, ZAIKA_RESTAURANT } from "@/features/guest/data/zaika-menu";

export interface GuestSessionState {
  data?: Awaited<ReturnType<typeof fetchGuestSession>>;
  errorKind?: SessionErrorKind;
  isPending: boolean;
}

export function useGuestSession(tableToken: string): GuestSessionState {
  const query = useQuery({
    queryKey: ["guest-session", tableToken],
    queryFn: () => fetchGuestSession(tableToken),
    retry: false,
    staleTime: 60_000,
  });
  return {
    data: query.data,
    errorKind: query.error ? mapSessionError(query.error) : undefined,
    isPending: query.isPending,
  };
}

export interface GuestMenuState {
  menu: GuestMenuResponse;
  /** True when the menu is the labelled Zaika sample dataset because
   * GET /guest/menu is not available yet. */
  isSample: boolean;
  isPending: boolean;
  error?: ApiError;
}

export function useGuestMenu(tableToken: string): GuestMenuState {
  const query = useQuery({
    queryKey: ["guest-menu", tableToken],
    queryFn: () => fetchGuestMenu(tableToken),
    retry: false,
    staleTime: 60_000,
  });

  if (query.data) {
    return { menu: query.data, isSample: false, isPending: false };
  }
  // Endpoint unavailable (404 — not implemented yet — or backend
  // unreachable): fall back to the labelled sample menu.
  return {
    menu: {
      categories: ZAIKA_CATEGORIES,
      items: ZAIKA_MENU_ITEMS,
    },
    isSample: true,
    isPending: query.isPending,
    error: query.error instanceof ApiError ? query.error : undefined,
  };
}

export function useGuestOrders(tableToken: string) {
  return useQuery({
    queryKey: ["guest-orders", tableToken],
    queryFn: () => fetchGuestOrders(tableToken),
    retry: false,
    staleTime: 15_000,
  });
}

export function useGuestOrder(tableToken: string, orderId: string) {
  return useQuery({
    queryKey: ["guest-order", tableToken, orderId],
    queryFn: () => fetchGuestOrder(tableToken, orderId),
    retry: false,
    staleTime: 15_000,
    // Tracking re-fetches via REST (api-plan.md § 9.4); WebSocket
    // push arrives in Stage 8. Polling is the Stage 2 mechanism.
    refetchInterval: 20_000,
  });
}

export function useGuestBill(tableToken: string) {
  return useQuery({
    queryKey: ["guest-bill", tableToken],
    queryFn: () => fetchGuestBill(tableToken),
    retry: false,
    staleTime: 15_000,
  });
}

/** Restaurant info for the header/hero. Sample data until the session
 * endpoint verifies the real restaurant (Stage 3). */
export function useGuestRestaurant(tableToken: string) {
  const session = useGuestSession(tableToken);
  return {
    restaurant: session.data?.restaurant ?? ZAIKA_RESTAURANT,
    isSample: !session.data,
    isPending: session.isPending,
    errorKind: session.errorKind,
  };
}
