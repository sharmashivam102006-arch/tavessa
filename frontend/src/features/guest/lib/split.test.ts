import { describe, expect, it } from "vitest";

import { equalSplit, validateCustomSplit } from "@/features/guest/lib/split";

describe("equalSplit", () => {
  it("splits an even total evenly", () => {
    expect(equalSplit(100000, 2)).toEqual([50000, 50000]);
  });

  it("gives the remainder paise to the earliest parts", () => {
    expect(equalSplit(100001, 3)).toEqual([33334, 33334, 33333]);
    expect(equalSplit(101, 2)).toEqual([51, 50]);
    expect(equalSplit(1000, 3)).toEqual([334, 333, 333]);
  });

  it("always sums exactly to the total (property check over totals)", () => {
    for (let total = 0; total < 500; total++) {
      for (const parts of [2, 3, 4, 5, 7]) {
        const split = equalSplit(total, parts);
        expect(split).toHaveLength(parts);
        expect(split.reduce((a, b) => a + b, 0)).toBe(total);
        expect(Math.max(...split) - Math.min(...split)).toBeLessThanOrEqual(1);
      }
    }
  });

  it("rejects fewer than 2 parts and negative totals", () => {
    expect(() => equalSplit(1000, 1)).toThrow();
    expect(() => equalSplit(-5, 2)).toThrow();
  });
});

describe("validateCustomSplit", () => {
  it("accepts amounts that sum exactly to the total", () => {
    expect(validateCustomSplit([40000, 60000], 100000).valid).toBe(true);
  });

  it("rejects amounts that do not sum to the total", () => {
    const result = validateCustomSplit([40000, 50000], 100000);
    expect(result.valid).toBe(false);
    expect(result.error).toContain("90000");
  });

  it("rejects zero or negative parts", () => {
    expect(validateCustomSplit([0, 100000], 100000).valid).toBe(false);
    expect(validateCustomSplit([-100, 100100], 100000).valid).toBe(false);
  });

  it("rejects fewer than 2 parts", () => {
    expect(validateCustomSplit([100000], 100000).valid).toBe(false);
  });
});
