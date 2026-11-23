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
import { KitchenLayout } from "@/features/kitchen/KitchenLayout";
import { KitchenPage } from "@/features/kitchen/KitchenPage";
import { StaffLayout } from "@/features/staff/StaffLayout";
import { StaffPage } from "@/features/staff/StaffPage";

/** Routes (docs/architecture.md § 4.2).
 *
 * The guest tree (/t/:tableToken) sits OUTSIDE AppLayout in its own
 * GuestLayout, so guests never see the dev backend-status pill.
 * The demo token "demo" opens the labelled Zaika sample menu
 * (Table 5 preview) — it is never presented as a verified session.
 * /kitchen likewise sits OUTSIDE AppLayout in its own full-width
 * KitchenLayout (Stage 4), and /staff in its own StaffLayout
 * (Stage 5). Admin keeps its Stage 1 shell. */
export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: "/", element: <HomePage /> },
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
    // The staff board is full-width: it sits OUTSIDE AppLayout in
    // its own StaffLayout (like the KDS) — AppLayout's max-w-5xl is
    // too narrow for a floor grid + orders + help calls.
    path: "/staff",
    element: <StaffLayout />,
    children: [{ index: true, element: <StaffPage /> }],
  },
  {
    // The KDS is full-width: it sits OUTSIDE AppLayout in its own
    // KitchenLayout (like the guest route's GuestLayout) — AppLayout's
    // max-w-5xl is too narrow for a wall screen.
    path: "/kitchen",
    element: <KitchenLayout />,
    children: [{ index: true, element: <KitchenPage /> }],
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
