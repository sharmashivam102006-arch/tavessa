import { useState } from "react";
import { Link, useParams } from "react-router";
import { Pencil, ShoppingBag, Trash2 } from "lucide-react";

import type { MenuItem } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";
import { priceForConfiguration } from "@/features/guest/lib/pricing";
import { useCartStore, type CartLine } from "@/features/guest/stores/cartStore";
import { useMenuItemLookup } from "@/features/guest/hooks/useGuest";
import { FoodImage } from "@/features/guest/components/FoodImage";
import { ItemDetailSheet } from "@/features/guest/components/ItemDetailSheet";
import { QuantityStepper } from "@/features/guest/components/QuantityStepper";
import { EmptyState } from "@/features/guest/components/states";
import { Button } from "@/components/ui/button";

function lineLabel(item: MenuItem, line: CartLine): string[] {
  const parts: string[] = [];
  if (line.variantId) {
    const variant = item.variants?.find((v) => v.id === line.variantId);
    if (variant) parts.push(variant.name);
  }
  for (const addOnId of line.addOnIds) {
    const addOn = item.addOns?.find((a) => a.id === addOnId);
    if (addOn) parts.push(addOn.name);
  }
  return parts;
}

/** Cart — line items with edit/remove/quantity, estimated totals.
 * Tax is undecided (decision D1): no tax line is invented. The
 * estimated total equals the subtotal for now, labelled as such. */
export function CartPage() {
  const { tableToken = "" } = useParams<{ tableToken: string }>();
  const lines = useCartStore((s) => s.lines);
  const setQuantity = useCartStore((s) => s.setQuantity);
  const removeLine = useCartStore((s) => s.removeLine);
  const clear = useCartStore((s) => s.clear);
  const findMenuItem = useMenuItemLookup(tableToken);
  const estimatedSubtotal = useCartStore((s) => s.estimatedSubtotal(findMenuItem));
  const [editing, setEditing] = useState<{ item: MenuItem; line: CartLine } | null>(null);

  if (lines.length === 0) {
    return (
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Your cart</h1>
        <div className="mt-4">
          <EmptyState
            icon={<ShoppingBag className="h-8 w-8 text-stone-400" />}
            title="Your cart is empty"
            body="Browse the menu and add something delicious — your cart stays saved on this table while you browse."
            action={
              <Button asChild>
                <Link to={`/t/${tableToken}`}>Browse the menu</Link>
              </Button>
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Your cart</h1>
        <Button variant="ghost" size="sm" onClick={clear}>
          <Trash2 className="h-4 w-4" />
          Clear cart
        </Button>
      </div>

      <ul className="mt-4 space-y-3">
        {lines.map((line) => {
          const item = findMenuItem(line.menuItemId);
          if (!item) return null;
          const unit = priceForConfiguration(item, line.variantId, line.addOnIds);
          return (
            <li
              key={line.key}
              className="flex gap-3 rounded-xl border border-stone-200 bg-white p-3 shadow-sm"
            >
              <FoodImage src={item.imageUrl} alt={item.name} className="h-20 w-20 shrink-0 rounded-lg" />
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-semibold leading-snug">{item.name}</p>
                  <p className="shrink-0 font-semibold tabular-nums">
                    {formatINR(unit * line.quantity)}
                  </p>
                </div>
                {lineLabel(item, line).length > 0 ? (
                  <p className="mt-0.5 text-xs text-stone-500">{lineLabel(item, line).join(" · ")}</p>
                ) : null}
                {line.specialInstructions ? (
                  <p className="mt-0.5 text-xs italic text-stone-500">
                    “{line.specialInstructions}”
                  </p>
                ) : null}
                <p className="mt-0.5 text-xs text-stone-500">{formatINR(unit)} each</p>
                <div className="mt-2 flex items-center gap-2">
                  <QuantityStepper
                    small
                    quantity={line.quantity}
                    onChange={(q) => setQuantity(line.key, q)}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setEditing({ item, line })}
                    className="h-8 text-brand-700"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeLine(line.key)}
                    className="h-8 text-red-700"
                    aria-label={`Remove ${item.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="mt-5 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
          Estimated bill
        </h2>
        <dl className="mt-2 space-y-1.5 text-sm">
          <div className="flex justify-between">
            <dt>Subtotal</dt>
            <dd className="font-medium tabular-nums">{formatINR(estimatedSubtotal)}</dd>
          </div>
          <div className="flex justify-between text-stone-500">
            <dt>Taxes</dt>
            <dd>Not configured yet (decision D1)</dd>
          </div>
          <div className="flex justify-between border-t border-stone-200 pt-2 text-base font-semibold">
            <dt>Estimated total</dt>
            <dd className="tabular-nums">{formatINR(estimatedSubtotal)}</dd>
          </div>
        </dl>
        <p className="mt-3 text-xs leading-relaxed text-stone-500">
          This is an estimate from the menu. The final payable amount is
          calculated and validated by the backend when the order is placed
          — the prices you see here are never the billing authority.
        </p>
        <Button asChild size="lg" className="mt-4 w-full">
          <Link to={`/t/${tableToken}/review`}>
            Review order · {formatINR(estimatedSubtotal)}
          </Link>
        </Button>
      </div>

      <ItemDetailSheet
        item={editing?.item ?? null}
        editingLine={editing?.line ?? null}
        onClose={() => setEditing(null)}
      />
    </div>
  );
}
