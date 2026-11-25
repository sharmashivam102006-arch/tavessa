// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { StrictMode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

// Vitest here runs without globals, so Testing Library's
// auto-cleanup does not register itself — clean up explicitly.
afterEach(() => cleanup());

import { bootRefresh } from "@/shared/api/client";
import { useAuthStore, type AuthUser } from "@/shared/stores/authStore";
import { BootAuth } from "@/features/auth/BootAuth";
import { RequireAuth } from "@/features/auth/RequireAuth";

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

function resetAuth() {
  useAuthStore.setState({
    accessToken: null,
    user: null,
    sessionExpired: false,
    status: "loading",
    bootAttempted: false,
  });
}

/** Mock fetch so only the refresh endpoint answers; anything else
 * is a test bug. `ok` decides the refresh outcome. */
function mockRefresh(ok: boolean, user: AuthUser = ownerUser()) {
  const fetchMock = vi.fn(async (url: unknown) => {
    const target = String(url);
    if (target.endsWith("/api/v1/auth/refresh")) {
      if (ok) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            data: { accessToken: "token-boot", user },
          }),
        };
      }
      return { ok: false, status: 401, json: async () => ({}) };
    }
    throw new Error(`unexpected fetch: ${target}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("bootRefresh (Stage 9 closure)", () => {
  beforeEach(() => {
    resetAuth();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("restores a valid session on boot — the reload case", async () => {
    const fetchMock = mockRefresh(true);
    const restored = await bootRefresh();

    expect(restored).toBe(true);
    expect(useAuthStore.getState().accessToken).toBe("token-boot");
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().user?.email).toBe("owner@zaika.example");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toContain("/api/v1/auth/refresh");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
  });

  it("expired cookie -> anonymous, no error surfaced", async () => {
    mockRefresh(false);
    const restored = await bootRefresh();

    expect(restored).toBe(false);
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(useAuthStore.getState().sessionExpired).toBe(false);
  });

  it("revoked refresh token -> 401 + anonymous (server revokes the family)", async () => {
    // The server-side half — reuse revokes the whole family — is
    // covered by backend tests/test_jwt_security.py
    // (test_refresh_reuse_revokes_whole_family). The frontend only
    // ever sees the 401 and must land on anonymous.
    mockRefresh(false);
    await bootRefresh();

    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("no second refresh after logout", async () => {
    const fetchMock = mockRefresh(false);
    await bootRefresh();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Log out (clears the in-memory token; the cookie is cleared
    // server-side by POST /logout). A re-render must not refresh.
    useAuthStore.getState().clearAuth();
    await bootRefresh();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  it("two concurrent boot refreshes make exactly one network call", async () => {
    const fetchMock = mockRefresh(true);
    const [a, b] = await Promise.all([bootRefresh(), bootRefresh()]);

    expect(a).toBe(true);
    expect(b).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("BootAuth under StrictMode still refreshes exactly once", async () => {
    const fetchMock = mockRefresh(true);
    render(
      <StrictMode>
        <BootAuth />
      </StrictMode>,
    );

    await waitFor(() =>
      expect(useAuthStore.getState().status).toBe("authenticated"),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("RequireAuth boot behaviour (Stage 9 closure)", () => {
  beforeEach(() => {
    resetAuth();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    cleanup();
  });

  function renderAdmin() {
    return render(
      <MemoryRouter initialEntries={["/admin"]}>
        <BootAuth />
        <Routes>
          <Route path="/admin" element={<RequireAuth />}>
            <Route index element={<div>Admin home</div>} />
          </Route>
          <Route path="/login" element={<div>Login page</div>} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("waits while loading instead of redirecting, then renders once restored", async () => {
    mockRefresh(true);
    renderAdmin();

    expect(screen.getByRole("status")).toHaveTextContent("Signing you in");
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();

    await waitFor(() =>
      expect(screen.getByText("Admin home")).toBeInTheDocument(),
    );
  });

  it("redirects to /login after a failed boot", async () => {
    mockRefresh(false);
    renderAdmin();

    await waitFor(() =>
      expect(screen.getByText("Login page")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Admin home")).not.toBeInTheDocument();
  });
});
