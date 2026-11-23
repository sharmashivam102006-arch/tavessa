/** Guest pricing — display-only helpers (pure functions).
 *
 * These compute what the guest SEES. The backend recomputes every
 * price from its own menu at order time (api-plan.md § 4.1); the
 * frontend never sends a price. All money is integer paise. */
import type { MenuItem } from "@/shared/api/types";

/** Unit price of one item in a chosen configuration:
 * base + selected variant delta + selected add-ons.
 * Unknown variant/add-on ids (not offered for this item) are ignored. */
export function priceForConfiguration(
  item: MenuItem,
  variantId?: string,
  addOnIds: string[] = [],
): number {
  let price = item.pricePaise;
  if (variantId) {
    const variant = item.variants?.find((v) => v.id === variantId);
    if (variant && variant.isAvailable) price += variant.priceDeltaPaise;
  }
  for (const addOnId of addOnIds) {
    const addOn = item.addOns?.find((a) => a.id === addOnId);
    if (addOn && addOn.isAvailable) price += addOn.pricePaise;
  }
  return Math.max(price, 0);
}

/** Line total for a quantity of one configuration. */
export function lineTotal(
  item: MenuItem,
  variantId: string | undefined,
  addOnIds: string[],
  quantity: number,
): number {
  return priceForConfiguration(item, variantId, addOnIds) * Math.max(quantity, 0);
}
