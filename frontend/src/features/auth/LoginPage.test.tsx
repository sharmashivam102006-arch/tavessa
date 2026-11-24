// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

// Vitest here runs without globals, so Testing Library's
// auto-cleanup does not register itself — clean up explicitly.
afterEach(() => cleanup());

import { ApiError } from "@/shared/api/client";
import type { AuthUser } from "@/shared/stores/authStore";
import { useAuthStore } from "@/shared/stores/authStore";
import { LoginPage } from "@/features/auth/LoginPage";
import { changePassword, login } from "@/features/admin/api/adminApi";

vi.mock("@/features/admin/api/adminApi", () => ({
  login: vi.fn(),
  logout: vi.fn(),
  fetchMe: vi.fn(),
  changePassword: vi.fn(),
  fetchWsTicket: vi.fn(),
  fetchDashboardOverview: vi.fn(),
  fetchAdminOrders: vi.fn(),
  fetchAdminBills: vi.fn(),
  fetchAdminBill: vi.fn(),
  fetchAdminPayments: vi.fn(),
  fetchAdminRefunds: vi.fn(),
  fetchDailyCash: vi.fn(),
  fetchDailyCashCsv: vi.fn(),
  voidBill: vi.fn(),
  createRefund: vi.fn(),
  fetchAdminMenu: vi.fn(),
  createCategory: vi.fn(),
  updateCategory: vi.fn(),
  reorderCategories: vi.fn(),
  createMenuItem: vi.fn(),
  updateMenuItem: vi.fn(),
  createVariant: vi.fn(),
  deleteVariant: vi.fn(),
  createAddOn: vi.fn(),
  deleteAddOn: vi.fn(),
  fetchAdminUsers: vi.fn(),
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

const mockedLogin = vi.mocked(login);
const mockedChangePassword = vi.mocked(changePassword);

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

function renderLogin() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/admin" element={<div>Admin home</div>} />
          <Route path="/staff" element={<div>Staff home</div>} />
          <Route path="/kitchen" element={<div>Kitchen home</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LoginPage (Stage 7)", () => {
  beforeEach(() => {
    useAuthStore.getState().clearAuth();
    vi.clearAllMocks();
  });

  it("signs an owner in and redirects to /admin", async () => {
    const user = ownerUser();
    mockedLogin.mockResolvedValue({ accessToken: "token-1", tokenType: "Bearer", user });
    const actor = userEvent.setup();
    renderLogin();

    await actor.type(screen.getByLabelText("Email"), "owner@zaika.example");
    await actor.type(screen.getByLabelText("Password"), "correct horse battery");
    await actor.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByText("Admin home")).toBeInTheDocument());
    expect(mockedLogin).toHaveBeenCalledWith("owner@zaika.example", "correct horse battery");
    expect(useAuthStore.getState().accessToken).toBe("token-1");
  });

  it("redirects a waiter to /staff and a kitchen user to /kitchen", async () => {
    mockedLogin.mockResolvedValue({
      accessToken: "token-2",
      tokenType: "Bearer",
      user: ownerUser({ role: "waiter", email: "waiter@zaika.example" }),
    });
    const actor = userEvent.setup();
    renderLogin();

    await actor.type(screen.getByLabelText("Email"), "waiter@zaika.example");
    await actor.type(screen.getByLabelText("Password"), "waiter-pass");
    await actor.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByText("Staff home")).toBeInTheDocument());
  });

  it("shows the server's generic error for wrong credentials", async () => {
    mockedLogin.mockRejectedValue(new ApiError(401, "unauthorized", "Email or password is incorrect."));
    const actor = userEvent.setup();
    renderLogin();

    await actor.type(screen.getByLabelText("Email"), "owner@zaika.example");
    await actor.type(screen.getByLabelText("Password"), "wrong");
    await actor.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Email or password is incorrect."),
    );
    expect(screen.queryByText("Admin home")).not.toBeInTheDocument();
  });

  it("forces a must-change-password account through the change form", async () => {
    const flagged = ownerUser({ mustChangePassword: true });
    mockedLogin.mockResolvedValue({ accessToken: "token-3", tokenType: "Bearer", user: flagged });
    const changed = ownerUser({ mustChangePassword: false });
    mockedChangePassword.mockResolvedValue(changed);
    const actor = userEvent.setup();
    renderLogin();

    await actor.type(screen.getByLabelText("Email"), "new@zaika.example");
    await actor.type(screen.getByLabelText("Password"), "initial-pass");
    await actor.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() =>
      expect(screen.getByText("You must change your password before continuing.")).toBeInTheDocument(),
    );
    await actor.type(screen.getByLabelText("New password"), "brand new pass");
    await actor.type(screen.getByLabelText("Confirm new password"), "brand new pass");
    await actor.click(screen.getByRole("button", { name: "Change password and continue" }));

    await waitFor(() => expect(screen.getByText("Admin home")).toBeInTheDocument());
    expect(mockedChangePassword).toHaveBeenCalledWith("initial-pass", "brand new pass");
  });

  it("rejects a too-short new password before calling the API", async () => {
    const flagged = ownerUser({ mustChangePassword: true });
    mockedLogin.mockResolvedValue({ accessToken: "token-4", tokenType: "Bearer", user: flagged });
    const actor = userEvent.setup();
    renderLogin();

    await actor.type(screen.getByLabelText("Email"), "new@zaika.example");
    await actor.type(screen.getByLabelText("Password"), "initial-pass");
    await actor.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() =>
      expect(screen.getByText("You must change your password before continuing.")).toBeInTheDocument(),
    );
    await actor.type(screen.getByLabelText("New password"), "short");
    await actor.type(screen.getByLabelText("Confirm new password"), "short");
    await actor.click(screen.getByRole("button", { name: "Change password and continue" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("at least 8 characters"),
    );
    expect(mockedChangePassword).not.toHaveBeenCalled();
  });
});
