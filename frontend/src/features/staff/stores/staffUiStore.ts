/** Staff UI store — header state shared between StaffLayout
 * (which renders it) and StaffPage (which owns the data).
 * Only header display state lives here; server state stays in
 * TanStack Query (useStaff.ts), never duplicated into Zustand.
 */
import { create } from "zustand";

import type { StaffConnectionStatus } from "@/shared/api/types";

interface StaffUiState {
  connection: StaffConnectionStatus;
  restaurantName: string | undefined;
  setConnection: (status: StaffConnectionStatus) => void;
  setRestaurantName: (name: string | undefined) => void;
}

export const useStaffUiStore = create<StaffUiState>()((set) => ({
  connection: "offline",
  restaurantName: undefined,
  setConnection: (connection) => set({ connection }),
  setRestaurantName: (restaurantName) => set({ restaurantName }),
}));
