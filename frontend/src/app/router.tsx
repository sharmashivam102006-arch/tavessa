import { createBrowserRouter } from "react-router";

import { HomePage } from "@/app/HomePage";
import { AppLayout } from "@/app/layouts/AppLayout";
import { RoleLayout } from "@/app/layouts/RoleLayout";
import { AdminPage } from "@/features/admin/AdminPage";
import { GuestPage } from "@/features/guest/GuestPage";
import { KitchenPage } from "@/features/kitchen/KitchenPage";
import { StaffPage } from "@/features/staff/StaffPage";

/** Routes (docs/architecture.md § 4.2). Guest is /t/:tableToken; the
 * demo token "demo" opens the Stage 1 placeholder. Real QR tokens
 * arrive in Stage 2. Staff/kitchen/admin require login from Stage 3+. */
export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: "/", element: <HomePage /> },
      {
        path: "/t/:tableToken",
        element: (
          <RoleLayout
            title="Guest"
            hint="Table ordering for Zaika — opens from a QR code."
          />
        ),
        children: [{ index: true, element: <GuestPage /> }],
      },
      {
        path: "/staff",
        element: (
          <RoleLayout
            title="Waiter / Staff"
            hint="Floor map, orders, help calls, cash collection."
          />
        ),
        children: [{ index: true, element: <StaffPage /> }],
      },
      {
        path: "/kitchen",
        element: (
          <RoleLayout
            title="Kitchen Display"
            hint="Live tickets by station."
          />
        ),
        children: [{ index: true, element: <KitchenPage /> }],
      },
      {
        path: "/admin",
        element: (
          <RoleLayout
            title="Owner / Admin"
            hint="Occupancy, sales, cash, menu, staff."
          />
        ),
        children: [{ index: true, element: <AdminPage /> }],
      },
      { path: "/login", element: <RoleLayout title="Sign in" hint="Staff and owner sign-in arrives in Stage 3." /> },
    ],
  },
]);
