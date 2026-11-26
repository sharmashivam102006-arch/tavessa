import { describe, expect, it } from "vitest";

import {
  buildKitchenWsUrl,
  columnForStatus,
  formatElapsed,
  nextBackoffDelayMs,
} from "@/features/kitchen/lib/kitchen";

describe("formatElapsed", () => {
  it("formats seconds as mm:ss", () => {
    expect(formatElapsed(0)).toBe("00:00");
    expect(formatElapsed(9)).toBe("00:09");
    expect(formatElapsed(65)).toBe("01:05");
    expect(formatElapsed(600)).toBe("10:00");
    expect(formatElapsed(3599)).toBe("59:59");
  });

  it("lets minutes run past 59 (long waits) and floors negatives", () => {
    expect(formatElapsed(3661)).toBe("61:01");
    expect(formatElapsed(-5)).toBe("00:00");
  });
});

describe("columnForStatus", () => {
  it("puts placed and accepted in New", () => {
    expect(columnForStatus("placed")).toBe("new");
    expect(columnForStatus("accepted")).toBe("new");
  });

  it("puts preparing in Preparing and ready in Ready", () => {
    expect(columnForStatus("preparing")).toBe("preparing");
    expect(columnForStatus("ready")).toBe("ready");
  });

  it("keeps terminal states off the board", () => {
    expect(columnForStatus("served")).toBeNull();
    expect(columnForStatus("completed")).toBeNull();
    expect(columnForStatus("cancelled")).toBeNull();
  });
});

describe("nextBackoffDelayMs", () => {
  it("doubles 1s, 2s, 4s, … and caps at 30s", () => {
    expect(nextBackoffDelayMs(0)).toBe(1000);
    expect(nextBackoffDelayMs(1)).toBe(2000);
    expect(nextBackoffDelayMs(2)).toBe(4000);
    expect(nextBackoffDelayMs(3)).toBe(8000);
    expect(nextBackoffDelayMs(4)).toBe(16_000);
    expect(nextBackoffDelayMs(5)).toBe(30_000);
    expect(nextBackoffDelayMs(10)).toBe(30_000);
    expect(nextBackoffDelayMs(-1)).toBe(1000);
  });
});

describe("buildKitchenWsUrl", () => {
  it("uses the page origin in dev (Vite proxies /ws)", () => {
    expect(
      buildKitchenWsUrl({
        apiBase: "",
        pageOrigin: "http://localhost:5173",
        restaurantId: "r-1",
        devKey: "dev-kitchen-only",
      }),
    ).toBe(
      "ws://localhost:5173/ws/kitchen?restaurant_id=r-1&dev_key=dev-kitchen-only",
    );
  });

  it("converts a production http(s) API base to ws(s)", () => {
    expect(
      buildKitchenWsUrl({
        apiBase: "https://api.example.com",
        pageOrigin: "https://example.com",
        restaurantId: "r-1",
        devKey: "k",
      }),
    ).toBe("wss://api.example.com/ws/kitchen?restaurant_id=r-1&dev_key=k");
    expect(
      buildKitchenWsUrl({
        apiBase: "http://api.example.com/",
        pageOrigin: "http://example.com",
        restaurantId: "r-1",
        devKey: "k",
      }),
    ).toBe("ws://api.example.com/ws/kitchen?restaurant_id=r-1&dev_key=k");
  });

  it("uses a configured wsBase directly (production: Render)", () => {
    expect(
      buildKitchenWsUrl({
        apiBase: "",
        wsBase: "wss://tavessa-api.onrender.com",
        pageOrigin: "https://tavessa-abc123.vercel.app",
        restaurantId: "r-1",
        ticket: "ticket-9",
      }),
    ).toBe(
      "wss://tavessa-api.onrender.com/ws/kitchen?restaurant_id=r-1&ticket=ticket-9",
    );
  });

  it("converts an https wsBase to wss and an http wsBase to ws", () => {
    expect(
      buildKitchenWsUrl({
        apiBase: "",
        wsBase: "https://tavessa-api.onrender.com/",
        pageOrigin: "https://tavessa-abc123.vercel.app",
        restaurantId: "r-1",
        ticket: "t",
      }),
    ).toBe("wss://tavessa-api.onrender.com/ws/kitchen?restaurant_id=r-1&ticket=t");
    expect(
      buildKitchenWsUrl({
        apiBase: "",
        wsBase: "http://localhost:8000",
        pageOrigin: "http://localhost:5173",
        restaurantId: "r-1",
        devKey: "k",
      }),
    ).toBe("ws://localhost:8000/ws/kitchen?restaurant_id=r-1&dev_key=k");
  });

  it("prefers wsBase over apiBase and strips a trailing slash", () => {
    expect(
      buildKitchenWsUrl({
        apiBase: "https://api.example.com",
        wsBase: "wss://tavessa-api.onrender.com/",
        pageOrigin: "https://tavessa-abc123.vercel.app",
        restaurantId: "r-1",
        ticket: "t",
      }),
    ).toBe("wss://tavessa-api.onrender.com/ws/kitchen?restaurant_id=r-1&ticket=t");
  });

  it("falls back to apiBase when wsBase is empty", () => {
    expect(
      buildKitchenWsUrl({
        apiBase: "https://api.example.com",
        wsBase: "",
        pageOrigin: "https://example.com",
        restaurantId: "r-1",
        devKey: "k",
      }),
    ).toBe("wss://api.example.com/ws/kitchen?restaurant_id=r-1&dev_key=k");
  });
});
