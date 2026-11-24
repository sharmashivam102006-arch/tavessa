/** Auth store — Stage 7.
 *
 * The access token lives IN MEMORY (Zustand) only — never
 * localStorage, never sessionStorage. The refresh token is an
 * HttpOnly cookie the browser handles (fetch with
 * credentials: "include"); this store never sees it.
 */
import { create } from "zustand";

export interface AuthUser {
  id: string;
  email: string;
  role: "owner" | "manager" | "waiter" | "kitchen";
  restaurantId: string;
  branchId: string | null;
  isActive: boolean;
  canManageStaff: boolean;
  canManageSettings: boolean;
  canRefund: boolean;
  canVoid: boolean;
  mustChangePassword: boolean;
  lastLoginAt: string | null;
}

interface AuthState {
  accessToken: string | null;
  user: AuthUser | null;
  sessionExpired: boolean;
  setAuth: (accessToken: string, user: AuthUser) => void;
  setUser: (user: AuthUser) => void;
  clearAuth: (sessionExpired?: boolean) => void;
}

export const useAuthStore = create<AuthState>()((set) => ({
  accessToken: null,
  user: null,
  sessionExpired: false,
  setAuth: (accessToken, user) =>
    set({ accessToken, user, sessionExpired: false }),
  setUser: (user) => set({ user }),
  clearAuth: (sessionExpired = false) =>
    set({ accessToken: null, user: null, sessionExpired }),
}));

export function landingRouteFor(role: AuthUser["role"]): string {
  switch (role) {
    case "owner":
    case "manager":
      return "/admin";
    case "waiter":
      return "/staff";
    case "kitchen":
      return "/kitchen";
  }
}

/** Capabilities the frontend hides navigation for (display-only;
 * the server enforces every one of these). */
export function canUse(user: AuthUser | null, capability: string): boolean {
  if (!user) return false;
  if (user.role === "owner") return true;
  const base: Record<string, string[]> = {
    view_dashboard: ["manager"],
    manage_settings: [],
    manage_menu: ["manager"],
    manage_staff: [],
    view_financials: ["manager"],
    confirm_cash: ["manager", "waiter"],
    serve_orders: ["manager", "waiter"],
    update_kitchen: ["manager", "kitchen"],
    view_kitchen: ["manager", "kitchen"],
    configure_tax: [],
    issue_refunds: [],
    void_bills: [],
  };
  if ((base[capability] ?? []).includes(user.role)) return true;
  if (user.role !== "manager") return false;
  const flag: Record<string, keyof AuthUser> = {
    manage_settings: "canManageSettings",
    manage_staff: "canManageStaff",
    issue_refunds: "canRefund",
    void_bills: "canVoid",
  };
  const key = flag[capability];
  return key ? Boolean(user[key]) : false;
}
