/** Guest cart — Zustand store, persisted per table token.
 *
 * - One cart per table token: the persistence key includes the token,
 *   and switching tokens loads that token's saved cart (or an empty
 *   one). The cart survives navigation within the session.
 * - A line is keyed by item + variant + add-ons + instructions, so
 *   the same dish with a different configuration is a separate line.
 * - Unavailable items can never be added (guard in `addLine`).
 * - Totals here are ESTIMATED display values. The backend recomputes
 *   every price at order time; the frontend never sends a total.
 */
import { create } from "zustand";

import type { MenuItem } from "@/shared/api/types";
import { lineTotal } from "@/features/guest/lib/pricing";

export interface CartLine {
  key: string;
  menuItemId: string;
  variantId?: string;
  addOnIds: string[];
  quantity: number;
  specialInstructions?: string;
}

export function cartLineKey(
  menuItemId: string,
  variantId: string | undefined,
  addOnIds: string[],
  specialInstructions?: string,
): string {
  const sorted = [...addOnIds].sort().join(",");
  return [menuItemId, variantId ?? "", sorted, (specialInstructions ?? "").trim()].join("|");
}

function cartStorageKey(tableToken: string): string {
  return `tavessa:cart:${tableToken}`;
}

function loadLines(tableToken: string): CartLine[] {
  if (typeof localStorage === "undefined" || !tableToken) return [];
  try {
    const raw = localStorage.getItem(cartStorageKey(tableToken));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as CartLine[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveLines(tableToken: string, lines: CartLine[]): void {
  if (typeof localStorage === "undefined" || !tableToken) return;
  try {
    localStorage.setItem(cartStorageKey(tableToken), JSON.stringify(lines));
  } catch {
    // Storage full/unavailable — the in-memory cart still works.
  }
}

interface CartState {
  tableToken: string;
  lines: CartLine[];
  /** Switch to a token's cart (loads its saved lines, or empty). */
  setTableToken: (token: string) => void;
  addLine: (item: MenuItem, config: Omit<CartLine, "key" | "menuItemId">) => boolean;
  removeLine: (key: string) => void;
  setQuantity: (key: string, quantity: number) => void;
  updateLine: (key: string, config: Omit<CartLine, "key" | "menuItemId">) => void;
  clear: () => void;
  /** Number of individual items (sum of quantities). */
  itemCount: () => number;
  /** Estimated subtotal in paise, from the sample menu display prices. */
  estimatedSubtotal: (lookup: (id: string) => MenuItem | undefined) => number;
}

export const useCartStore = create<CartState>()((set, get) => {
  const persist = (lines: CartLine[]) => {
    const { tableToken } = get();
    saveLines(tableToken, lines);
  };

  return {
    tableToken: "",
    lines: [],

    setTableToken: (token) => {
      if (token === get().tableToken) return;
      set({ tableToken: token, lines: loadLines(token) });
    },

    addLine: (item, config) => {
      // Guard: unavailable items cannot be ordered.
      if (!item.isAvailable) return false;
      const key = cartLineKey(item.id, config.variantId, config.addOnIds, config.specialInstructions);
      const existing = get().lines.find((line) => line.key === key);
      const lines = existing
        ? get().lines.map((line) =>
            line.key === key
              ? { ...line, quantity: Math.min(line.quantity + config.quantity, 20) }
              : line,
          )
        : [...get().lines, { key, menuItemId: item.id, ...config }];
      set({ lines });
      persist(lines);
      return true;
    },

    removeLine: (key) => {
      const lines = get().lines.filter((line) => line.key !== key);
      set({ lines });
      persist(lines);
    },

    setQuantity: (key, quantity) => {
      const clamped = Math.max(0, Math.min(Math.floor(quantity), 20));
      const lines =
        clamped === 0
          ? get().lines.filter((line) => line.key !== key)
          : get().lines.map((line) =>
              line.key === key ? { ...line, quantity: clamped } : line,
            );
      set({ lines });
      persist(lines);
    },

    updateLine: (key, config) => {
      const line = get().lines.find((l) => l.key === key);
      if (!line) return;
      const newKey = cartLineKey(line.menuItemId, config.variantId, config.addOnIds, config.specialInstructions);
      const rest = get().lines.filter((l) => l.key !== key);
      const clash = rest.find((l) => l.key === newKey);
      const lines = clash
        ? rest.map((l) =>
            l.key === newKey
              ? { ...l, quantity: Math.min(l.quantity + config.quantity, 20) }
              : l,
          )
        : [...rest, { key: newKey, menuItemId: line.menuItemId, ...config }];
      set({ lines });
      persist(lines);
    },

    clear: () => {
      set({ lines: [] });
      persist([]);
    },

    itemCount: () => get().lines.reduce((sum, line) => sum + line.quantity, 0),

    estimatedSubtotal: (lookup) =>
      get().lines.reduce((sum, line) => {
        const item = lookup(line.menuItemId);
        if (!item) return sum;
        return sum + lineTotal(item, line.variantId, line.addOnIds, line.quantity);
      }, 0),
  };
});
