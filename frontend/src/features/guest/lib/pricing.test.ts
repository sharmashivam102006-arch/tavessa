import { describe, expect, it } from "vitest";

import type { MenuItem } from "@/shared/api/types";
import { lineTotal, priceForConfiguration } from "@/features/guest/lib/pricing";

const ITEM: MenuItem = {
  id: "butter-chicken",
  categoryId: "main-course",
  name: "Butter Chicken",
  description: "",
  pricePaise: 34900,
  isVeg: false,
  isAvailable: true,
  isPopular: true,
  station: "Main Kitchen",
  variantGroupLabel: "Portion",
  variants: [
    { id: "half", name: "Half", priceDeltaPaise: -10000, isAvailable: true },
    { id: "full", name: "Full", priceDeltaPaise: 0, isAvailable: true },
    { id: "gone", name: "Gone", priceDeltaPaise: 5000, isAvailable: false },
  ],
  addOns: [
    { id: "gravy", name: "Extra gravy", pricePaise: 5000, isAvailable: true },
    { id: "butter", name: "Extra butter", pricePaise: 2000, isAvailable: true },
    { id: "soldout", name: "Sold out add-on", pricePaise: 9000, isAvailable: false },
  ],
};

describe("priceForConfiguration", () => {
  it("returns the base price with no options", () => {
    expect(priceForConfiguration(ITEM)).toBe(34900);
  });

  it("applies a negative variant delta (Half portion)", () => {
    expect(priceForConfiguration(ITEM, "half")).toBe(24900);
  });

  it("adds add-on prices", () => {
    expect(priceForConfiguration(ITEM, "full", ["gravy", "butter"])).toBe(41900);
  });

  it("combines variant delta and add-ons", () => {
    expect(priceForConfiguration(ITEM, "half", ["gravy"])).toBe(29900);
  });

  it("ignores variant/add-on ids that do not belong to the item", () => {
    expect(priceForConfiguration(ITEM, "not-a-variant", ["not-an-addon"])).toBe(34900);
  });

  it("ignores unavailable variants and add-ons", () => {
    expect(priceForConfiguration(ITEM, "gone", ["soldout"])).toBe(34900);
  });

  it("never goes below zero", () => {
    const cheap: MenuItem = { ...ITEM, pricePaise: 100, variants: [{ id: "v", name: "V", priceDeltaPaise: -500, isAvailable: true }] };
    expect(priceForConfiguration(cheap, "v")).toBe(0);
  });
});

describe("lineTotal", () => {
  it("multiplies the configured unit price by quantity", () => {
    expect(lineTotal(ITEM, "half", ["gravy"], 2)).toBe(29900 * 2);
  });

  it("is zero for zero quantity", () => {
    expect(lineTotal(ITEM, undefined, [], 0)).toBe(0);
  });
});
