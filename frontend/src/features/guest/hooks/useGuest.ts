/** TanStack Query hooks for the guest interface.
 *
 * Stage 3: the backend guest endpoints exist. Real tokens use the real
 * API and surface errors to the UI — there is no silent sample fallback
 * for a normal token. The labelled Zaika sample dataset is kept ONLY
 * for the `/t/demo` preview (tableToken === "demo").
 * - useGuestSession: retry:false. On failure the UI renders the
 *   invalid/expired/not-connected state mapped from the error —
 *   never a fake verified session.
 * - useGuestMenu: tries GET /guest/menu; only the demo token falls
 *   back to the labelled sample menu (`isSample: true`). Any other
 *   token returns an empty menu plus the error, so the UI can say so.
 * - useGuestOrders / useGuestOrder / useGuestBill: no fallback —
 *   orders and bills must never be invented. On failure the UI shows
 *   the not-connected state.
 */
import { useQuery } from "@tanstack/react-query";

import { ApiError, mapSessionError, type SessionErrorKind } from "@/shared/api/client";
import type { GuestMenuResponse, MenuItem } from "@/shared/api/types";
import {
  fetchGuestBill,
  fetchGuestHelpCalls,
  fetchGuestMenu,
  fetchGuestOrder,
  fetchGuestOrders,
  fetchGuestReceipt,
  fetchGuestSession,
} from "@/features/guest/api/guestApi";
import {
  ZAIKA_CATEGORIES,
  ZAIKA_MENU_ITEMS,
  ZAIKA_RESTAURANT,
  findMenuItem,
} from "@/features/guest/data/zaika-menu";

/** True only for the labelled demo preview — the one token allowed to
 * use the sample dataset. */
export function isDemoToken(tableToken: string): boolean {
  return tableToken === "demo";
}

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
  /** True only for the /t/demo preview, where the menu is the
   * labelled Zaika sample dataset. False for every real token —
   * a failed menu fetch for a real token is an error, not a sample. */
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
  if (isDemoToken(tableToken)) {
    // Demo preview only: the labelled sample menu.
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
  // Real token, menu unavailable: no silent fallback — surface the error.
  return {
    menu: { categories: [], items: [] },
    isSample: false,
    isPending: query.isPending,
    error: query.error instanceof ApiError ? query.error : undefined,
  };
}

/** Resolve a cart line's menu item id to its MenuItem, from the real
 * menu when loaded, from the sample dataset only for the demo token.
 * (Cart lines store backend UUIDs for real tokens; the sample
 * `findMenuItem` only knows the demo ids.) */
export function useMenuItemLookup(
  tableToken: string,
): (id: string) => MenuItem | undefined {
  const query = useQuery({
    queryKey: ["guest-menu", tableToken],
    queryFn: () => fetchGuestMenu(tableToken),
    retry: false,
    staleTime: 60_000,
  });
  return (id: string) => {
    if (query.data) return query.data.items.find((item) => item.id === id);
    if (isDemoToken(tableToken)) return findMenuItem(id);
    return undefined;
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
    // Live updates arrive over the guest WebSocket (Stage 8):
    // the socket invalidates this query on order events and on
    // (re)connect. No interval polling — the socket hook refetches
    // on a slow interval only while the socket is down.
  });
}

/** The session's help calls, newest first (Stage 8). The Help page
 * refetches this after a socket reconnect; help_call.* events
 * invalidate it live. */
export function useGuestHelpCalls(tableToken: string) {
  return useQuery({
    queryKey: ["guest-help-calls", tableToken],
    queryFn: () => fetchGuestHelpCalls(tableToken),
    retry: false,
    staleTime: 15_000,
  });
}

export function useGuestBill(tableToken: string) {
  return useQuery({
    queryKey: ["guest-bill", tableToken],
    queryFn: () => fetchGuestBill(tableToken),
    retry: false,
    staleTime: 15_000,
    // Live updates arrive over the guest WebSocket (Stage 8):
    // bill.*/payment.* events invalidate this query. No interval
    // polling — the socket hook refetches on a slow interval only
    // while the socket is down. A paid bill never changes again.
  });
}

/** Receipt data for the paid bill. Only enabled once the bill is
 * paid — before that the endpoint 409s, so the hook stays idle. */
export function useGuestReceipt(tableToken: string, billPaid: boolean) {
  return useQuery({
    queryKey: ["guest-receipt", tableToken],
    queryFn: () => fetchGuestReceipt(tableToken),
    retry: false,
    staleTime: 60_000,
    enabled: billPaid,
  });
}

/** Restaurant info for the header/hero. Real data once the session
 * endpoint verifies it; the sample restaurant only for /t/demo.
 * `restaurant` is undefined for a real token whose session has not
 * verified yet — callers must handle that (MenuPage shows the
 * not-verified banner). */
export function useGuestRestaurant(tableToken: string) {
  const session = useGuestSession(tableToken);
  return {
    restaurant: session.data?.restaurant ?? (isDemoToken(tableToken) ? ZAIKA_RESTAURANT : undefined),
    isSample: isDemoToken(tableToken) && !session.data,
    isPending: session.isPending,
    errorKind: session.errorKind,
  };
}
