import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  createIdempotencyKey,
  mapSessionError,
} from "@/shared/api/client";
import { useAuthStore, type AuthUser } from "@/shared/stores/authStore";

function ownerUser(): AuthUser {
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

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

describe("mapSessionError", () => {
  it("maps 401 invalid_table_token to invalid_token", () => {
    expect(mapSessionError(new ApiError(401, "invalid_table_token", "bad token"))).toBe("invalid_token");
  });

  it("maps 410 session_expired to expired", () => {
    expect(mapSessionError(new ApiError(410, "session_expired", "expired"))).toBe("expired");
  });

  it("maps 403 to forbidden", () => {
    expect(mapSessionError(new ApiError(403, "forbidden", "no"))).toBe("forbidden");
  });

  it("maps a 404 (endpoint not implemented yet) to not_connected, not invalid_token", () => {
    expect(mapSessionError(new ApiError(404, "not_found", "no such route"))).toBe("not_connected");
  });

  it("maps a network failure to not_connected", () => {
    expect(mapSessionError(new TypeError("Failed to fetch"))).toBe("not_connected");
  });
});

describe("createIdempotencyKey", () => {
  it("returns a UUID and a fresh value on every call", () => {
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    const a = createIdempotencyKey();
    const b = createIdempotencyKey();
    expect(a).toMatch(uuid);
    expect(b).toMatch(uuid);
    expect(a).not.toBe(b);
  });
});

describe("silent refresh on 401 (Stage 7, pinned in the Stage 9 closure)", () => {
  beforeEach(() => {
    resetAuth();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("retries once after a successful refresh, with the new token", async () => {
    useAuthStore.getState().setAuth("token-old", ownerUser());
    const fetchMock = vi.fn();
    fetchMock.mockImplementation(async (url: unknown, init?: RequestInit) => {
      const target = String(url);
      if (target.endsWith("/api/v1/auth/refresh")) {
        return jsonResponse(200, {
          data: { accessToken: "token-new", user: ownerUser() },
        });
      }
      const headers = init?.headers as Record<string, string> | undefined;
      if (headers?.Authorization === "Bearer token-old") {
        return jsonResponse(401, {
          error: { code: "unauthenticated", message: "expired" },
        });
      }
      return jsonResponse(200, { data: ["ok"] });
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.get<{ data: string[] }>("/api/v1/admin/users");

    expect(result).toEqual({ data: ["ok"] });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(useAuthStore.getState().accessToken).toBe("token-new");
    const thirdCall = fetchMock.mock.calls[2];
    expect((thirdCall[1] as RequestInit)?.headers).toMatchObject({
      Authorization: "Bearer token-new",
    });
  });

  it("fails once when the refresh fails — no retry loop", async () => {
    useAuthStore.getState().setAuth("token-old", ownerUser());
    const fetchMock = vi.fn(async (url: unknown) => {
      const target = String(url);
      if (target.endsWith("/api/v1/auth/refresh")) {
        return jsonResponse(401, {
          error: { code: "unauthenticated", message: "no session" },
        });
      }
      return jsonResponse(401, {
        error: { code: "unauthenticated", message: "expired" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.get("/api/v1/admin/users")).rejects.toMatchObject({
      status: 401,
    });
    // Initial call + one refresh attempt. A loop would call more.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  it("does not attempt a refresh without an access token", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(401, {
        error: { code: "unauthenticated", message: "no token" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.get("/api/v1/admin/users")).rejects.toMatchObject({
      status: 401,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry 401s from /auth/* paths", async () => {
    useAuthStore.getState().setAuth("token-old", ownerUser());
    const fetchMock = vi.fn(async () =>
      jsonResponse(401, {
        error: { code: "unauthenticated", message: "bad login" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.get("/api/v1/auth/me")).rejects.toMatchObject({
      status: 401,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
