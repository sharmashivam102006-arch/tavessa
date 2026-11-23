import { createBrowserRouter } from "react-router";

import { HomePage } from "@/app/HomePage";
import { AppLayout } from "@/app/layouts/AppLayout";
import { RoleLayout } from "@/app/layouts/RoleLayout";
import { AdminPage } from "@/features/admin/AdminPage";
import { BillPage } from "@/features/guest/BillPage";
import { CartPage } from "@/features/guest/CartPage";
import { GuestLayout } from "@/features/guest/GuestLayout";
import { HelpPage } from "@/features/guest/HelpPage";
import { MenuPage } from "@/features/guest/MenuPage";
import { OrderConfirmationPage } from "@/features/guest/OrderConfirmationPage";
import { OrderReviewPage } from "@/features/guest/OrderReviewPage";
import { OrdersPage } from "@/features/guest/OrdersPage";
import { OrderTrackingPage } from "@/features/guest/OrderTrackingPage";
import { KitchenPage } from "@/features/kitchen/KitchenPage";
import { StaffPage } from "@/features/staff/StaffPage";

/** Routes (docs/architecture.md § 4.2).
 *
 * The guest tree (/t/:tableToken) sits OUTSIDE AppLayout in its own
 * GuestLayout, so guests never see the dev backend-status pill.
 * The demo token "demo" opens the labelled Zaika sample menu
 * (Table 5 preview) — it is never presented as a verified session.
 * Staff/kitchen/admin keep their Stage 1 shells, unchanged. */
export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: "/", element: <HomePage /> },
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
  {
    path: "/t/:tableToken",
    element: <GuestLayout />,
    children: [
      { index: true, element: <MenuPage /> },
      { path: "cart", element: <CartPage /> },
      { path: "review", element: <OrderReviewPage /> },
      { path: "confirmation", element: <OrderConfirmationPage /> },
      { path: "orders", element: <OrdersPage /> },
      { path: "orders/:orderId", element: <OrderTrackingPage /> },
      { path: "bill", element: <BillPage /> },
      { path: "help", element: <HelpPage /> },
    ],
  },
]);
