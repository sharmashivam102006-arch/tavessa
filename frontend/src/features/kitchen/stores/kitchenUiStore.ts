/** Kitchen UI store — header state shared between KitchenLayout
 * (which renders it) and KitchenPage (which owns the data).
 * Only header display state lives here; server state stays in
 * TanStack Query (useKitchen.ts), never duplicated into Zustand.
 */
import { create } from "zustand";

import type { KitchenConnectionStatus } from "@/shared/api/types";

interface KitchenUiState {
  connection: KitchenConnectionStatus;
  restaurantName: string | undefined;
  setConnection: (status: KitchenConnectionStatus) => void;
  setRestaurantName: (name: string | undefined) => void;
}

export const useKitchenUiStore = create<KitchenUiState>()((set) => ({
  connection: "offline",
  restaurantName: undefined,
  setConnection: (connection) => set({ connection }),
  setRestaurantName: (restaurantName) => set({ restaurantName }),
}));
