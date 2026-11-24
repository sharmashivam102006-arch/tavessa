// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, cleanup, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

// Vitest here runs without globals, so Testing Library's
// auto-cleanup does not register itself — clean up explicitly.
afterEach(() => cleanup());

import { ApiError } from "@/shared/api/client";
import type {
  AdminBill,
  AdminCategory,
  AdminMenuItem,
  AdminPayment,
  AdminRefund,
  AdminUser,
  AuditLogRow,
  DashboardOverview,
} from "@/shared/api/types";
import { useAuthStore, type AuthUser } from "@/shared/stores/authStore";
import { AdminLayout } from "@/features/admin/AdminLayout";
import { AuditPage } from "@/features/admin/pages/AuditPage";
import { BillsPage } from "@/features/admin/pages/BillsPage";
import { MenuPage } from "@/features/admin/pages/MenuPage";
import { OverviewPage } from "@/features/admin/pages/OverviewPage";
import { RefundsPage } from "@/features/admin/pages/RefundsPage";
import { SettingsPage } from "@/features/admin/pages/SettingsPage";
import { StaffPage } from "@/features/admin/pages/StaffPage";
import {
  createRefund,
  createTaxConfig,
  createUser,
  deactivateUser,
  fetchAdminBills,
  fetchAdminPayments,
  fetchAdminRefunds,
  fetchAuditLogs,
  fetchBranches,
  fetchCategories,
  fetchDashboard,
  fetchMenuItems,
  fetchRestaurantSettings,
  fetchTax,
  fetchUsers,
  updateMenuItem,
  voidBill,
} from "@/features/admin/api/adminApi";

vi.mock("@/features/admin/api/adminApi", () => ({
  login: vi.fn(),
  logout: vi.fn(),
  fetchMe: vi.fn(),
  changePassword: vi.fn(),
  fetchWsTicket: vi.fn(),
  fetchDashboard: vi.fn(),
  fetchAdminOrders: vi.fn(),
  fetchAdminBills: vi.fn(),
  fetchAdminBill: vi.fn(),
  fetchAdminPayments: vi.fn(),
  fetchAdminRefunds: vi.fn(),
  fetchDailyCash: vi.fn(),
  fetchDailyCashCsv: vi.fn(),
  voidBill: vi.fn(),
  createRefund: vi.fn(),
  fetchCategories: vi.fn(),
  fetchMenuItems: vi.fn(),
  createCategory: vi.fn(),
  updateCategory: vi.fn(),
  reorderCategories: vi.fn(),
  createMenuItem: vi.fn(),
  updateMenuItem: vi.fn(),
  createVariant: vi.fn(),
  deleteVariant: vi.fn(),
  createAddOn: vi.fn(),
  deleteAddOn: vi.fn(),
  fetchUsers: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  deactivateUser: vi.fn(),
  resetUserPassword: vi.fn(),
  fetchRestaurantSettings: vi.fn(),
  updateRestaurantSettings: vi.fn(),
  fetchBranches: vi.fn(),
  updateBranch: vi.fn(),
  fetchTables: vi.fn(),
  createTable: vi.fn(),
  fetchTax: vi.fn(),
  createTaxConfig: vi.fn(),
  fetchAuditLogs: vi.fn(),
}));

const mockedUpdateMenuItem = vi.mocked(updateMenuItem);
const mockedCreateUser = vi.mocked(createUser);
const mockedDeactivateUser = vi.mocked(deactivateUser);
const mockedCreateTaxConfig = vi.mocked(createTaxConfig);
const mockedVoidBill = vi.mocked(voidBill);
const mockedCreateRefund = vi.mocked(createRefund);
const mockedFetchAuditLogs = vi.mocked(fetchAuditLogs);

function ownerUser(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: "user-owner",
    email: "owner@zaika.example",
    role: "owner",
    restaurantId: "rest-1",
    branchId: null,
    isActive: true,
    lastLoginAt: null,
    mustChangePassword: false,
    canManageStaff: true,
    canManageSettings: true,
    canRefund: true,
    canVoid: true,
    ...overrides,
  };
}

function signIn(user: AuthUser) {
  useAuthStore.setState({ accessToken: "test-token", user, sessionExpired: false });
}

function renderPage(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function overview(overrides: Partial<DashboardOverview> = {}): DashboardOverview {
  return {
    restaurantId: "rest-1",
    branchId: null,
    periodStart: new Date().toISOString(),
    periodEnd: new Date().toISOString(),
    preset: "today",
    timezone: "Asia/Kolkata",
    grossSalesPaise: 54800,
    cashCollectedPaise: 30000,
    refundsPaise: 5000,
    netCashPaise: 25000,
    outstandingPaise: 24800,
    billCount: 4,
    orderCount: 5,
    averageOrderValuePaise: 13700,
    ordersByStatus: [
      { status: "placed", count: 1 },
      { status: "preparing", count: 1 },
      { status: "ready", count: 1 },
      { status: "served", count: 2 },
    ],
    recentPayments: [],
    recentActivity: [],
    bestSellers: [
      { menuItemId: "item-1", name: "Paneer Butter Masala", quantity: 7, revenuePaise: 174300 },
    ],
    ...overrides,
  };
}

function bill(overrides: Partial<AdminBill> = {}): AdminBill {
  return {
    id: "bill-1",
    billNumber: "B-2026-11-24-001",
    tableNumber: 5,
    status: "issued",
    totalPaise: 54800,
    amountPaidPaise: 0,
    collectedPaise: 0,
    outstandingPaise: 54800,
    refundPaise: 0,
    netCollectedPaise: 0,
    issuedAt: new Date().toISOString(),
    paidAt: null,
    voidedAt: null,
    voidReason: null,
    ...overrides,
  };
}

function payment(overrides: Partial<AdminPayment> = {}): AdminPayment {
  return {
    id: "pay-1",
    receiptNumber: "R-2026-11-24-001",
    billId: "bill-1",
    billNumber: "B-2026-11-24-001",
    amountPaise: 54800,
    method: "cash",
    status: "confirmed",
    collectedAt: new Date().toISOString(),
    collectedByName: "Waiter One",
    ...overrides,
  };
}

function category(overrides: Partial<AdminCategory> = {}): AdminCategory {
  return {
    id: "cat-1",
    name: "Mains",
    description: null,
    sortOrder: 1,
    isActive: true,
    itemCount: 1,
    ...overrides,
  };
}

function menuItem(): AdminMenuItem {
  return {
    id: "item-1",
    categoryId: "cat-1",
    categoryName: "Mains",
    name: "Paneer Butter Masala",
    description: "Rich and creamy",
    pricePaise: 24900,
    isVeg: true,
    isAvailable: true,
    isPopular: true,
    imageUrl: null,
    station: "Tandoor",
    sortOrder: 1,
    variants: [],
    addOns: [],
  };
}

function adminUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: "user-waiter",
    email: "waiter@zaika.example",
    role: "waiter",
    restaurantId: "rest-1",
    branchId: null,
    isActive: true,
    mustChangePassword: false,
    canManageStaff: false,
    canManageSettings: false,
    canRefund: false,
    canVoid: false,
    createdAt: new Date().toISOString(),
    lastLoginAt: null,
    ...overrides,
  };
}

function page<T>(data: T[], total = data.length) {
  return {
    data,
    pagination: { page: 1, per_page: 50, total, total_pages: Math.max(1, Math.ceil(total / 50)) },
  };
}

describe("Admin pages (Stage 7)", () => {
  beforeEach(() => {
    useAuthStore.getState().clearAuth();
    signIn(ownerUser());
    vi.clearAllMocks();
  });

  it("overview renders separate gross sales, cash collected, refunds, net and outstanding", async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(overview());
    renderPage(<OverviewPage />);

    await waitFor(() => expect(screen.getByText("₹548.00")).toBeInTheDocument());
    expect(screen.getByText("Gross sales")).toBeInTheDocument();
    expect(screen.getByText("Cash collected")).toBeInTheDocument();
    expect(screen.getByText("Refunds")).toBeInTheDocument();
    expect(screen.getByText("Net cash")).toBeInTheDocument();
    expect(screen.getByText("Outstanding")).toBeInTheDocument();
    // Gross sales and cash collected are separate figures — unpaid
    // bills are not labelled revenue collected.
    expect(screen.getByText("Paneer Butter Masala")).toBeInTheDocument();
    expect(screen.getByText("placed")).toBeInTheDocument();
    expect(screen.getByText(/Average order value:/)).toBeInTheDocument();
    expect(screen.getByText(/5 orders/)).toBeInTheDocument();
  });

  it("overview shows an empty state when there are no bills", async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(
      overview({ grossSalesPaise: 0, cashCollectedPaise: 0, refundsPaise: 0, netCashPaise: 0, outstandingPaise: 0, billCount: 0, orderCount: 0, averageOrderValuePaise: 0, ordersByStatus: [], bestSellers: [] }),
    );
    renderPage(<OverviewPage />);
    await waitFor(() => expect(screen.getByText("No orders in this period.")).toBeInTheDocument());
    expect(screen.getByText("No items sold in this period.")).toBeInTheDocument();
  });

  it("overview shows a loading state then an error state with retry", async () => {
    vi.mocked(fetchDashboard).mockRejectedValue(new ApiError(500, "server_error", "boom"));
    renderPage(<OverviewPage />);
    expect(screen.getByText("Loading overview…")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Could not load the overview.")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("menu page renders categories and items, and a price edit calls the API", async () => {
    vi.mocked(fetchCategories).mockResolvedValue([category()]);
    vi.mocked(fetchMenuItems).mockResolvedValue(page([menuItem()]));
    vi.mocked(updateMenuItem).mockResolvedValue(menuItem());
    const actor = userEvent.setup();
    renderPage(<MenuPage />);

    await waitFor(() => expect(screen.getByText("Paneer Butter Masala")).toBeInTheDocument());
    const input = screen.getByLabelText("Price for Paneer Butter Masala in rupees");
    await actor.clear(input);
    await actor.type(input, "299");
    await actor.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mockedUpdateMenuItem).toHaveBeenCalledWith("item-1", { pricePaise: 29900 }),
    );
    await waitFor(() =>
      expect(screen.getByText(/Existing bills are unchanged/)).toBeInTheDocument(),
    );
  });

  it("staff page creates a user and deactivates one only after confirmation", async () => {
    vi.mocked(fetchUsers).mockResolvedValue(page([adminUser()]));
    vi.mocked(createUser).mockResolvedValue(adminUser({ id: "user-new", email: "new@zaika.example" }));
    vi.mocked(deactivateUser).mockResolvedValue(adminUser({ isActive: false }));
    const actor = userEvent.setup();
    renderPage(<StaffPage />);

    await waitFor(() => expect(screen.getByText("waiter@zaika.example")).toBeInTheDocument());
    await actor.type(screen.getByLabelText("New user email"), "new@zaika.example");
    await actor.type(screen.getByLabelText("Initial password"), "initial-pass");
    await actor.click(screen.getByRole("button", { name: "Create user" }));
    await waitFor(() =>
      expect(mockedCreateUser).toHaveBeenCalledWith(
        expect.objectContaining({ email: "new@zaika.example", role: "waiter" }),
      ),
    );

    await actor.click(screen.getByRole("button", { name: "Deactivate" }));
    expect(screen.getByText("Deactivate waiter@zaika.example?")).toBeInTheDocument();
    expect(mockedDeactivateUser).not.toHaveBeenCalled();
    await actor.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(mockedDeactivateUser).toHaveBeenCalledWith("user-waiter"));
  });

  it("settings page shows the unconfigured tax state clearly", async () => {
    vi.mocked(fetchRestaurantSettings).mockResolvedValue({
      id: "rest-1",
      name: "Zaika",
      slug: "zaika",
      tagline: null,
      cuisine: null,
      address: null,
      phone: null,
      contactName: null,
      contactEmail: null,
      openingHours: null,
      currency: "INR",
    });
    vi.mocked(fetchBranches).mockResolvedValue([]);
    vi.mocked(fetchTax).mockResolvedValue({ current: null, history: [] });
    const actor = userEvent.setup();
    renderPage(<SettingsPage />);

    await waitFor(() =>
      expect(screen.getByText(/Tax: not configured/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/not a 0% rate/)).toBeInTheDocument();

    await actor.click(screen.getByRole("button", { name: "Save tax config" }));
    await waitFor(() =>
      expect(mockedCreateTaxConfig).toHaveBeenCalledWith(
        expect.objectContaining({ enabled: true, rateBps: 500, mode: "exclusive", label: "GST" }),
      ),
    );
  });

  it("bills page voids a bill only after a reason and confirmation", async () => {
    vi.mocked(fetchAdminBills).mockResolvedValue(page([bill()]));
    vi.mocked(voidBill).mockResolvedValue(undefined);
    const actor = userEvent.setup();
    renderPage(<BillsPage />);

    await waitFor(() => expect(screen.getByText("B-2026-11-24-001")).toBeInTheDocument());
    await actor.click(screen.getByRole("button", { name: "Void" }));
    expect(screen.getByText("Void this bill?")).toBeInTheDocument();
    expect(mockedVoidBill).not.toHaveBeenCalled();
    await actor.type(screen.getByLabelText("Reason (required)"), "Wrong table");
    await actor.click(screen.getByRole("button", { name: "Void bill" }));
    await waitFor(() =>
      expect(mockedVoidBill).toHaveBeenCalledWith("bill-1", "Wrong table"),
    );
  });

  it("refunds page creates a refund only after confirmation", async () => {
    vi.mocked(fetchAdminPayments).mockResolvedValue(page([payment()]));
    vi.mocked(fetchAdminRefunds).mockResolvedValue(page([] as AdminRefund[]));
    const refund: AdminRefund = {
      id: "ref-1",
      refundReference: "RFD-2026-11-24-001",
      billId: "bill-1",
      billNumber: "B-2026-11-24-001",
      paymentId: "pay-1",
      receiptNumber: "R-2026-11-24-001",
      amountPaise: 10000,
      reason: "Wrong dish",
      refundedByName: "Owner",
      refundedAt: new Date().toISOString(),
    };
    vi.mocked(createRefund).mockResolvedValue(refund);
    const actor = userEvent.setup();
    renderPage(<RefundsPage />);

    await waitFor(() => expect(screen.getByText("No refunds recorded yet.")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByRole("option", { name: /R-2026-11-24-001/ })).toBeInTheDocument(),
    );
    await actor.selectOptions(screen.getByLabelText("Payment to refund"), "pay-1");
    await actor.type(screen.getByLabelText("Refund amount in rupees"), "100");
    await actor.type(screen.getByLabelText("Refund reason"), "Wrong dish");
    await actor.click(screen.getByRole("button", { name: "Review refund" }));

    expect(screen.getByText("Confirm refund?")).toBeInTheDocument();
    expect(mockedCreateRefund).not.toHaveBeenCalled();
    await actor.click(screen.getByRole("button", { name: "Record refund" }));
    await waitFor(() =>
      expect(mockedCreateRefund).toHaveBeenCalledWith("bill-1", "pay-1", 10000, "Wrong dish"),
    );
  });

  it("audit page renders rows and filters by action", async () => {
    const row: AuditLogRow = {
      id: "audit-1",
      action: "refund.created",
      entityType: "refund",
      entityId: "ref-1",
      details: { amount_paise: 10000 },
      createdAt: new Date().toISOString(),
    };
    vi.mocked(fetchAuditLogs).mockResolvedValue(page([row]));
    const actor = userEvent.setup();
    renderPage(<AuditPage />);

    await waitFor(() => expect(screen.getByText("refund.created")).toBeInTheDocument());
    await actor.type(screen.getByLabelText("Filter by action"), "refund.created");
    await waitFor(() =>
      expect(mockedFetchAuditLogs).toHaveBeenCalledWith(
        expect.objectContaining({ action: "refund.created" }),
      ),
    );
  });
});

describe("AdminLayout role-based navigation (Stage 7)", () => {
  beforeEach(() => {
    useAuthStore.getState().clearAuth();
    vi.clearAllMocks();
  });

  function renderLayout(user: AuthUser) {
    signIn(user);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/admin"]}>
          <Routes>
            <Route path="/admin" element={<AdminLayout />}>
              <Route index element={<div>Overview content</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("shows every section to the owner", () => {
    renderLayout(ownerUser());
    expect(screen.getByRole("link", { name: "Menu" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Staff" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Refunds" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Audit log" })).toBeInTheDocument();
    expect(screen.getByText("owner@zaika.example")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log out" })).toBeInTheDocument();
  });

  it("hides menu, staff and settings from a waiter (display-only gating)", () => {
    renderLayout(
      ownerUser({
        role: "waiter",
        canManageStaff: false,
        canManageSettings: false,
        canRefund: false,
        canVoid: false,
      }),
    );
    const nav = screen.getByRole("navigation");
    expect(within(nav).queryByText("Menu")).not.toBeInTheDocument();
    expect(within(nav).queryByText("Staff")).not.toBeInTheDocument();
    expect(within(nav).queryByText("Settings")).not.toBeInTheDocument();
    expect(within(nav).queryByText("Refunds")).not.toBeInTheDocument();
  });
});
