import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";

import type { MenuItem } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";
import { priceForConfiguration } from "@/features/guest/lib/pricing";
import { useCartStore, type CartLine } from "@/features/guest/stores/cartStore";
import { FoodImage } from "@/features/guest/components/FoodImage";
import { QuantityStepper } from "@/features/guest/components/QuantityStepper";
import { VegBadge } from "@/features/guest/components/VegBadge";
import { Button } from "@/components/ui/button";

export interface DetailSelection {
  variantId?: string;
  addOnIds: string[];
  quantity: number;
  specialInstructions: string;
}

function defaultSelection(item: MenuItem, line?: CartLine): DetailSelection {
  const firstAvailableVariant = item.variants?.find((v) => v.isAvailable);
  return {
    variantId: line?.variantId ?? firstAvailableVariant?.id,
    addOnIds: line?.addOnIds ?? [],
    quantity: line?.quantity ?? 1,
    specialInstructions: line?.specialInstructions ?? "",
  };
}

/** Item detail + customisation sheet (bottom sheet on mobile, centred
 * dialog on desktop). Only shows the options the item actually
 * offers: variants (radio), add-ons (checkboxes), quantity and
 * special instructions. Price updates live as options change. */
export function ItemDetailSheet({
  item,
  editingLine,
  onClose,
}: {
  item: MenuItem | null;
  /** When set, the sheet edits this cart line instead of adding new. */
  editingLine?: CartLine | null;
  onClose: () => void;
}) {
  const addLine = useCartStore((s) => s.addLine);
  const updateLine = useCartStore((s) => s.updateLine);
  const [selection, setSelection] = useState<DetailSelection>(() =>
    item ? defaultSelection(item, editingLine ?? undefined) : { addOnIds: [], quantity: 1, specialInstructions: "" },
  );

  // Reset the form whenever a different item/line is opened.
  useEffect(() => {
    if (item) setSelection(defaultSelection(item, editingLine ?? undefined));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id, editingLine?.key]);

  const unitPrice = useMemo(
    () => (item ? priceForConfiguration(item, selection.variantId, selection.addOnIds) : 0),
    [item, selection.variantId, selection.addOnIds],
  );

  if (!item) return null;

  const toggleAddOn = (addOnId: string) => {
    setSelection((s) => ({
      ...s,
      addOnIds: s.addOnIds.includes(addOnId)
        ? s.addOnIds.filter((id) => id !== addOnId)
        : [...s.addOnIds, addOnId],
    }));
  };

  const submit = () => {
    if (editingLine) {
      updateLine(editingLine.key, {
        variantId: selection.variantId,
        addOnIds: selection.addOnIds,
        quantity: selection.quantity,
        specialInstructions: selection.specialInstructions.trim() || undefined,
      });
    } else {
      addLine(item, {
        variantId: selection.variantId,
        addOnIds: selection.addOnIds,
        quantity: selection.quantity,
        specialInstructions: selection.specialInstructions.trim() || undefined,
      });
    }
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label={item.name}
      onClick={onClose}
    >
      <div
        className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-t-2xl bg-white shadow-xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="relative">
          <FoodImage src={item.imageUrl} alt={item.name} className="h-52 w-full sm:h-60" />
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={onClose}
            className="absolute right-3 top-3 h-9 w-9 rounded-full p-0 shadow"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </Button>
          {!item.isAvailable ? (
            <span className="absolute bottom-3 left-3 rounded-full bg-stone-900 px-3 py-1 text-xs font-semibold text-white">
              Sold out right now
            </span>
          ) : null}
        </div>

        <div className="p-5">
          <div className="flex items-start gap-2">
            <VegBadge isVeg={item.isVeg} className="mt-1" />
            <div>
              <h2 className="text-xl font-semibold leading-snug">{item.name}</h2>
              <p className="mt-1 text-sm leading-relaxed text-stone-600">{item.description}</p>
            </div>
          </div>
          <p className="mt-3 text-lg font-semibold tabular-nums">{formatINR(unitPrice)}</p>

          {item.variants?.length ? (
            <fieldset className="mt-5">
              <legend className="text-sm font-semibold">
                {item.variantGroupLabel ?? "Choose one"}
                <span className="ml-2 text-xs font-normal text-stone-500">Required</span>
              </legend>
              <div className="mt-2 space-y-2">
                {item.variants.map((variant) => {
                  const checked = selection.variantId === variant.id;
                  return (
                    <label
                      key={variant.id}
                      className={`flex cursor-pointer items-center justify-between rounded-lg border px-3 py-2.5 text-sm ${
                        checked ? "border-brand-600 bg-brand-50" : "border-stone-300"
                      } ${variant.isAvailable ? "" : "opacity-50"}`}
                    >
                      <span className="flex items-center gap-2">
                        <input
                          type="radio"
                          name={`variant-${item.id}`}
                          checked={checked}
                          disabled={!variant.isAvailable}
                          onChange={() => setSelection((s) => ({ ...s, variantId: variant.id }))}
                          className="accent-brand-700"
                        />
                        {variant.name}
                      </span>
                      <span className="tabular-nums text-stone-600">
                        {variant.priceDeltaPaise === 0
                          ? "Included"
                          : variant.priceDeltaPaise > 0
                            ? `+ ${formatINR(variant.priceDeltaPaise)}`
                            : `− ${formatINR(-variant.priceDeltaPaise)}`}
                      </span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          ) : null}

          {item.addOns?.length ? (
            <fieldset className="mt-5">
              <legend className="text-sm font-semibold">
                Add-ons <span className="ml-2 text-xs font-normal text-stone-500">Optional</span>
              </legend>
              <div className="mt-2 space-y-2">
                {item.addOns.map((addOn) => {
                  const checked = selection.addOnIds.includes(addOn.id);
                  return (
                    <label
                      key={addOn.id}
                      className={`flex cursor-pointer items-center justify-between rounded-lg border px-3 py-2.5 text-sm ${
                        checked ? "border-brand-600 bg-brand-50" : "border-stone-300"
                      } ${addOn.isAvailable ? "" : "opacity-50"}`}
                    >
                      <span className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={!addOn.isAvailable}
                          onChange={() => toggleAddOn(addOn.id)}
                          className="accent-brand-700"
                        />
                        {addOn.name}
                      </span>
                      <span className="tabular-nums text-stone-600">+ {formatINR(addOn.pricePaise)}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          ) : null}

          <div className="mt-5">
            <label htmlFor="special-instructions" className="text-sm font-semibold">
              Special instructions <span className="text-xs font-normal text-stone-500">Optional</span>
            </label>
            <textarea
              id="special-instructions"
              rows={2}
              maxLength={200}
              value={selection.specialInstructions}
              onChange={(e) => setSelection((s) => ({ ...s, specialInstructions: e.target.value }))}
              placeholder="e.g. Less spicy, no onion, extra crispy"
              className="mt-2 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm outline-none focus:border-brand-600 focus:ring-1 focus:ring-brand-600"
            />
          </div>

          <div className="mt-6 flex items-center justify-between gap-3 border-t border-stone-200 pt-4">
            <QuantityStepper
              quantity={selection.quantity}
              onChange={(q) => setSelection((s) => ({ ...s, quantity: Math.max(1, q) }))}
            />
            <Button
              type="button"
              size="lg"
              disabled={!item.isAvailable}
              onClick={submit}
              className="flex-1"
            >
              {editingLine ? "Update item" : "Add to cart"} · {formatINR(unitPrice * selection.quantity)}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
