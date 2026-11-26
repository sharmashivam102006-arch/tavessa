import { describe, expect, it } from "vitest";

import type { KitchenOrder } from "@/shared/api/types";
import {
  buildStaffWsUrl,
  nextBackoffDelayMs,
  sortOrdersReadyFirst,
  statusBadgeForTable,
} from "@/features/staff/lib/staff";

function order(overrides: Partial<KitchenOrder> = {}): KitchenOrder {
  return {
    id: "order-1",
    orderNumber: "ZAIKA-0001",
    tableSessionId: "session-1",
    status: "placed",
    items: [],
    subtotalPaise: 1000,
    taxPaise: 0,
    totalPaise: 1000,
    placedAt: new Date().toISOString(),
    tableNumber: 5,
    elapsedSeconds: 0,
    ...overrides,
  };
}

describe("nextBackoffDelayMs", () => {
  it("doubles from 1s and caps at 30s", () => {
    expect(nextBackoffDelayMs(0)).toBe(1000);
    expect(nextBackoffDelayMs(1)).toBe(2000);
    expect(nextBackoffDelayMs(2)).toBe(4000);
    expect(nextBackoffDelayMs(5)).toBe(30_000);
    expect(nextBackoffDelayMs(10)).toBe(30_000);
  });

  it("clamps negative attempts to 1s", () => {
    expect(nextBackoffDelayMs(-3)).toBe(1000);
  });
});

describe("buildStaffWsUrl", () => {
  it("uses the page origin in dev (no VITE_API_URL)", () => {
    expect(
      buildStaffWsUrl({
        apiBase: "",
        pageOrigin: "http://localhost:5173",
        restaurantId: "rest-1",
        devKey: "dev-staff-only",
      }),
    ).toBe(
      "ws://localhost:5173/ws/staff?restaurant_id=rest-1&dev_key=dev-staff-only",
    );
  });

  it("converts an http(s) API base to ws(s)", () => {
    expect(
      buildStaffWsUrl({
        apiBase: "https://api.example.com/",
        pageOrigin: "https://app.example.com",
        restaurantId: "rest-1",
        devKey: "k",
      }),
    ).toBe("wss://api.example.com/ws/staff?restaurant_id=rest-1&dev_key=k");
  });

  it("uses a configured wsBase directly (production: Render)", () => {
    expect(
      buildStaffWsUrl({
        apiBase: "",
        wsBase: "wss://tavessa-api.onrender.com",
        pageOrigin: "https://tavessa-abc123.vercel.app",
        restaurantId: "rest-1",
        ticket: "ticket-9",
      }),
    ).toBe(
      "wss://tavessa-api.onrender.com/ws/staff?restaurant_id=rest-1&ticket=ticket-9",
    );
  });

  it("converts an https wsBase to wss and an http wsBase to ws", () => {
    expect(
      buildStaffWsUrl({
        apiBase: "",
        wsBase: "https://tavessa-api.onrender.com/",
        pageOrigin: "https://tavessa-abc123.vercel.app",
        restaurantId: "rest-1",
        ticket: "t",
      }),
    ).toBe("wss://tavessa-api.onrender.com/ws/staff?restaurant_id=rest-1&ticket=t");
    expect(
      buildStaffWsUrl({
        apiBase: "",
        wsBase: "http://localhost:8000",
        pageOrigin: "http://localhost:5173",
        restaurantId: "rest-1",
        devKey: "k",
      }),
    ).toBe("ws://localhost:8000/ws/staff?restaurant_id=rest-1&dev_key=k");
  });

  it("prefers wsBase over apiBase and strips a trailing slash", () => {
    expect(
      buildStaffWsUrl({
        apiBase: "https://api.example.com",
        wsBase: "wss://tavessa-api.onrender.com/",
        pageOrigin: "https://tavessa-abc123.vercel.app",
        restaurantId: "rest-1",
        ticket: "t",
      }),
    ).toBe("wss://tavessa-api.onrender.com/ws/staff?restaurant_id=rest-1&ticket=t");
  });

  it("falls back to apiBase when wsBase is empty", () => {
    expect(
      buildStaffWsUrl({
        apiBase: "https://api.example.com",
        wsBase: "",
        pageOrigin: "https://example.com",
        restaurantId: "rest-1",
        devKey: "k",
      }),
    ).toBe("wss://api.example.com/ws/staff?restaurant_id=rest-1&dev_key=k");
  });
});

describe("sortOrdersReadyFirst", () => {
  it("puts ready orders first, oldest first within each group", () => {
    const placed = order({
      id: "a",
      status: "placed",
      placedAt: "2026-11-23T10:00:00Z",
    });
    const readyOld = order({
      id: "b",
      status: "ready",
      placedAt: "2026-11-23T09:00:00Z",
    });
    const readyNew = order({
      id: "c",
      status: "ready",
      placedAt: "2026-11-23T09:30:00Z",
    });
    const preparing = order({
      id: "d",
      status: "preparing",
      placedAt: "2026-11-23T08:00:00Z",
    });
    expect(
      sortOrdersReadyFirst([placed, readyNew, preparing, readyOld]).map(
        (o) => o.id,
      ),
    ).toEqual(["b", "c", "d", "a"]);
  });

  it("does not mutate the input", () => {
    const input = [order({ status: "placed" }), order({ status: "ready" })];
    const sorted = sortOrdersReadyFirst(input);
    expect(input[0].status).toBe("placed");
    expect(sorted[0].status).toBe("ready");
  });
});

describe("statusBadgeForTable", () => {
  it("labels each dining-table status", () => {
    expect(statusBadgeForTable("available").label).toBe("Available");
    expect(statusBadgeForTable("occupied").label).toBe("Occupied");
    expect(statusBadgeForTable("cleaning").label).toBe("Cleaning");
    expect(statusBadgeForTable("out_of_service").label).toBe("Out of service");
  });
});
