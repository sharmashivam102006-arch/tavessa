import { describe, expect, it } from "vitest";

import { formatINR } from "@/shared/utils/money";

describe("formatINR (Stage 9)", () => {
  it("formats ₹0", () => {
    expect(formatINR(0)).toBe("₹0.00");
  });

  it("formats a single paise", () => {
    expect(formatINR(1)).toBe("₹0.01");
  });

  it("formats odd paise exactly (no rounding to rupees)", () => {
    expect(formatINR(101)).toBe("₹1.01");
    expect(formatINR(199)).toBe("₹1.99");
    expect(formatINR(999)).toBe("₹9.99");
  });

  it("formats whole rupees", () => {
    expect(formatINR(100)).toBe("₹1.00");
    expect(formatINR(4900)).toBe("₹49.00");
  });

  it("uses Indian digit grouping", () => {
    expect(formatINR(100000)).toBe("₹1,000.00");
    expect(formatINR(123456)).toBe("₹1,234.56");
    expect(formatINR(12345678)).toBe("₹1,23,456.78");
  });

  it("formats a large bill total", () => {
    expect(formatINR(105001)).toBe("₹1,050.01");
  });
});
