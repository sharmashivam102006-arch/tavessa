import { createBrowserRouter } from "react-router";

import { HomePage } from "@/app/HomePage";
import { AppLayout } from "@/app/layouts/AppLayout";
import { AdminLayout } from "@/features/admin/AdminLayout";
import { AuditPage } from "@/features/admin/pages/AuditPage";
import { BillsPage } from "@/features/admin/pages/BillsPage";
import { CashPage } from "@/features/admin/pages/CashPage";
import { MenuPage } from "@/features/admin/pages/MenuPage";
import { OrdersPage } from "@/features/admin/pages/OrdersPage";
import { OverviewPage } from "@/features/admin/pages/OverviewPage";
import { RefundsPage } from "@/features/admin/pages/RefundsPage";
import { SettingsPage } from "@/features/admin/pages/SettingsPage";
import { StaffPage as AdminStaffPage } from "@/features/admin/pages/StaffPage";
import { TablesPage } from "@/features/admin/pages/TablesPage";
import { LoginPage } from "@/features/auth/LoginPage";
import { RequireAuth, RequireStaffAuth } from "@/features/auth/RequireAuth";
import { BillPage } from "@/features/guest/BillPage";
import { CartPage } from "@/features/guest/CartPage";
import { GuestLayout } from "@/features/guest/GuestLayout";
import { HelpPage } from "@/features/guest/HelpPage";
import { MenuPage as GuestMenuPage } from "@/features/guest/MenuPage";
import { OrderConfirmationPage } from "@/features/guest/OrderConfirmationPage";
import { OrderReviewPage } from "@/features/guest/OrderReviewPage";
import { OrdersPage as GuestOrdersPage } from "@/features/guest/OrdersPage";
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
 * (Stage 5). /admin (Stage 7) has its own AdminLayout with a
 * sidebar; /login serves staff, kitchen and owner sign-in. */
export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { path: "/", element: <HomePage /> },
      { path: "/login", element: <LoginPage /> },
    ],
  },
  {
    path: "/admin",
    element: <RequireAuth />,
    children: [
      {
        element: <AdminLayout />,
        children: [
          { index: true, element: <OverviewPage /> },
          { path: "orders", element: <OrdersPage /> },
          { path: "menu", element: <MenuPage /> },
          { path: "staff", element: <AdminStaffPage /> },
          { path: "tables", element: <TablesPage /> },
          { path: "bills", element: <BillsPage /> },
          { path: "cash", element: <CashPage /> },
          { path: "refunds", element: <RefundsPage /> },
          { path: "audit-logs", element: <AuditPage /> },
          { path: "settings", element: <SettingsPage /> },
        ],
      },
    ],
  },
  {
    // The staff board is full-width: it sits OUTSIDE AppLayout in
    // its own StaffLayout (like the KDS) — AppLayout's max-w-5xl is
    // too narrow for a floor grid + orders + help calls. In
    // production builds a login is required (Stage 7); in DEV the
    // dev-key flow still works.
    path: "/staff",
    element: <RequireStaffAuth />,
    children: [
      {
        element: <StaffLayout />,
        children: [{ index: true, element: <StaffPage /> }],
      },
    ],
  },
  {
    // The KDS is full-width: it sits OUTSIDE AppLayout in its own
    // KitchenLayout (like the guest route's GuestLayout) — AppLayout's
    // max-w-5xl is too narrow for a wall screen. Production builds
    // require a login (Stage 7); DEV keeps the dev-key flow.
    path: "/kitchen",
    element: <RequireStaffAuth />,
    children: [
      {
        element: <KitchenLayout />,
        children: [{ index: true, element: <KitchenPage /> }],
      },
    ],
  },
  {
    path: "/t/:tableToken",
    element: <GuestLayout />,
    children: [
      { index: true, element: <GuestMenuPage /> },
      { path: "cart", element: <CartPage /> },
      { path: "review", element: <OrderReviewPage /> },
      { path: "confirmation", element: <OrderConfirmationPage /> },
      { path: "orders", element: <GuestOrdersPage /> },
      { path: "orders/:orderId", element: <OrderTrackingPage /> },
      { path: "bill", element: <BillPage /> },
      { path: "help", element: <HelpPage /> },
    ],
  },
]);
