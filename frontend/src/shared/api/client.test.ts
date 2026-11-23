import { describe, expect, it } from "vitest";

import {
  ApiError,
  createIdempotencyKey,
  mapSessionError,
} from "@/shared/api/client";

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
