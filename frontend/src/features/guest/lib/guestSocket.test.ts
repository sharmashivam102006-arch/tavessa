// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import {
  buildGuestWsUrl,
  nextGuestBackoffDelayMs,
} from "@/features/guest/lib/guestSocket";

describe("nextGuestBackoffDelayMs", () => {
  it("starts at ~1s with jitter and doubles per attempt", () => {
    // Jitter is ±25% around the base, so each attempt's delay must
    // land inside [0.75×base, 1.25×base].
    for (let attempt = 0; attempt < 4; attempt++) {
      const base = 1000 * 2 ** attempt;
      for (let i = 0; i < 20; i++) {
        const delay = nextGuestBackoffDelayMs(attempt);
        expect(delay).toBeGreaterThanOrEqual(0.75 * base);
        expect(delay).toBeLessThanOrEqual(1.25 * base);
      }
    }
  });

  it("caps at 30s even after jitter", () => {
    for (let i = 0; i < 50; i++) {
      expect(nextGuestBackoffDelayMs(10)).toBeLessThanOrEqual(30_000);
      expect(nextGuestBackoffDelayMs(5)).toBeLessThanOrEqual(30_000);
    }
  });

  it("is not deterministic — jitter varies the delay", () => {
    const delays = new Set(
      Array.from({ length: 20 }, () => nextGuestBackoffDelayMs(2)),
    );
    expect(delays.size).toBeGreaterThan(1);
  });

  it("treats negative attempts as attempt 0", () => {
    const delay = nextGuestBackoffDelayMs(-3);
    expect(delay).toBeGreaterThanOrEqual(750);
    expect(delay).toBeLessThanOrEqual(1250);
  });
});

describe("buildGuestWsUrl", () => {
  it("uses the page origin in dev (no api base)", () => {
    expect(
      buildGuestWsUrl({
        apiBase: "",
        pageOrigin: "http://localhost:5173",
        ticket: "t-1",
      }),
    ).toBe("ws://localhost:5173/ws/guest?ticket=t-1");
  });

  it("converts an https api base to wss", () => {
    expect(
      buildGuestWsUrl({
        apiBase: "https://signspeak.example.com/",
        pageOrigin: "https://signspeak.example.com",
        ticket: "t-2",
      }),
    ).toBe("wss://signspeak.example.com/ws/guest?ticket=t-2");
  });

  it("URL-encodes the ticket", () => {
    const url = buildGuestWsUrl({
      apiBase: "",
      pageOrigin: "http://localhost:5173",
      ticket: "a/b+c=d",
    });
    expect(url).toContain("ticket=a%2Fb%2Bc%3Dd");
    expect(url).not.toContain("ticket=a/b");
  });
});
