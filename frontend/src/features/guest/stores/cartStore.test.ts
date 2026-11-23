import { beforeEach, describe, expect, it } from "vitest";

import { findMenuItem } from "@/features/guest/data/zaika-menu";
import { useCartStore } from "@/features/guest/stores/cartStore";

const butterChicken = findMenuItem("butter-chicken")!;
const paneerTikka = findMenuItem("paneer-tikka")!;
const soldOut = findMenuItem("hara-bhara-kebab")!;

describe("cart store", () => {
  beforeEach(() => {
    useCartStore.setState({ tableToken: "test-token", lines: [] });
  });

  it("adds a line and merges an identical configuration", () => {
    const store = useCartStore.getState();
    expect(store.addLine(butterChicken, { variantId: "portion-full", addOnIds: [], quantity: 1 })).toBe(true);
    useCartStore.getState().addLine(butterChicken, { variantId: "portion-full", addOnIds: [], quantity: 2 });
    const lines = useCartStore.getState().lines;
    expect(lines).toHaveLength(1);
    expect(lines[0].quantity).toBe(3);
  });

  it("keeps a different variant as a separate line", () => {
    const store = useCartStore.getState();
    store.addLine(butterChicken, { variantId: "portion-full", addOnIds: [], quantity: 1 });
    useCartStore.getState().addLine(butterChicken, { variantId: "portion-half", addOnIds: [], quantity: 1 });
    expect(useCartStore.getState().lines).toHaveLength(2);
  });

  it("keeps different add-ons and instructions as separate lines", () => {
    const store = useCartStore.getState();
    store.addLine(paneerTikka, { addOnIds: [], quantity: 1 });
    useCartStore.getState().addLine(paneerTikka, { addOnIds: ["extra-chutney"], quantity: 1 });
    useCartStore.getState().addLine(paneerTikka, { addOnIds: [], quantity: 1, specialInstructions: "less spicy" });
    expect(useCartStore.getState().lines).toHaveLength(3);
  });

  it("refuses unavailable items", () => {
    expect(soldOut.isAvailable).toBe(false);
    expect(useCartStore.getState().addLine(soldOut, { addOnIds: [], quantity: 1 })).toBe(false);
    expect(useCartStore.getState().lines).toHaveLength(0);
  });

  it("removes a line when quantity is set to 0 and clamps to 20", () => {
    const store = useCartStore.getState();
    store.addLine(butterChicken, { variantId: "portion-full", addOnIds: [], quantity: 1 });
    const key = useCartStore.getState().lines[0].key;
    useCartStore.getState().setQuantity(key, 99);
    expect(useCartStore.getState().lines[0].quantity).toBe(20);
    useCartStore.getState().setQuantity(key, 0);
    expect(useCartStore.getState().lines).toHaveLength(0);
  });

  it("updates a line's configuration and merges on clash", () => {
    const store = useCartStore.getState();
    store.addLine(butterChicken, { variantId: "portion-full", addOnIds: [], quantity: 1 });
    store.addLine(butterChicken, { variantId: "portion-half", addOnIds: [], quantity: 2 });
    const halfKey = useCartStore.getState().lines.find((l) => l.variantId === "portion-half")!.key;
    useCartStore.getState().updateLine(halfKey, { variantId: "portion-full", addOnIds: [], quantity: 2 });
    const lines = useCartStore.getState().lines;
    expect(lines).toHaveLength(1);
    expect(lines[0].quantity).toBe(3);
  });

  it("computes the estimated subtotal from display prices", () => {
    const store = useCartStore.getState();
    // Half butter chicken (34900 − 10000) + extra gravy (5000) = 29900, ×2
    store.addLine(butterChicken, { variantId: "portion-half", addOnIds: ["extra-gravy"], quantity: 2 });
    // Paneer tikka 24900 ×1
    useCartStore.getState().addLine(paneerTikka, { addOnIds: [], quantity: 1 });
    expect(useCartStore.getState().estimatedSubtotal(findMenuItem)).toBe(29900 * 2 + 24900);
    expect(useCartStore.getState().itemCount()).toBe(3);
  });

  it("clears the cart", () => {
    const store = useCartStore.getState();
    store.addLine(butterChicken, { variantId: "portion-full", addOnIds: [], quantity: 1 });
    useCartStore.getState().clear();
    expect(useCartStore.getState().lines).toHaveLength(0);
  });

  it("switches carts when the table token changes", () => {
    const store = useCartStore.getState();
    store.addLine(butterChicken, { variantId: "portion-full", addOnIds: [], quantity: 1 });
    useCartStore.getState().setTableToken("another-table");
    expect(useCartStore.getState().lines).toHaveLength(0);
    expect(useCartStore.getState().tableToken).toBe("another-table");
  });
});
