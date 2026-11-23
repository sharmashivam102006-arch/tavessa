import { Plus, Star } from "lucide-react";

import type { MenuItem } from "@/shared/api/types";
import { formatINR } from "@/shared/utils/money";
import { FoodImage } from "@/features/guest/components/FoodImage";
import { VegBadge } from "@/features/guest/components/VegBadge";
import { Button } from "@/components/ui/button";

/** One menu item card. Tapping anywhere opens the detail sheet;
 * the ADD button opens it too (customisation lives in the sheet).
 * Sold-out items show as such and cannot be added. */
export function MenuItemCard({
  item,
  onOpen,
}: {
  item: MenuItem;
  onOpen: (item: MenuItem) => void;
}) {
  return (
    <article
      className={`flex gap-3 rounded-xl border bg-white p-3 shadow-sm ${
        item.isAvailable ? "border-stone-200" : "border-stone-200 opacity-75"
      }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <VegBadge isVeg={item.isVeg} />
          {item.isPopular ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
              <Star className="h-3 w-3 fill-amber-500 text-amber-500" />
              Popular
            </span>
          ) : null}
          {!item.isAvailable ? (
            <span className="rounded-full bg-stone-100 px-2 py-0.5 text-[11px] font-semibold text-stone-600">
              Sold out
            </span>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => onOpen(item)}
          className="mt-1.5 block text-left text-base font-semibold leading-snug text-stone-900 hover:text-brand-700"
        >
          {item.name}
        </button>
        <p className="mt-0.5 line-clamp-2 text-sm leading-snug text-stone-600">
          {item.description}
        </p>
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-base font-semibold tabular-nums text-stone-900">
            {formatINR(item.pricePaise)}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!item.isAvailable}
            onClick={() => onOpen(item)}
            className="border-brand-600 text-brand-700 hover:bg-brand-50 disabled:border-stone-300 disabled:text-stone-400"
          >
            <Plus className="h-4 w-4" />
            {item.variants?.length || item.addOns?.length ? "Customise" : "Add"}
          </Button>
        </div>
      </div>
      <button
        type="button"
        onClick={() => onOpen(item)}
        className="relative h-24 w-24 shrink-0 overflow-hidden rounded-lg sm:h-28 sm:w-28"
        aria-label={`View ${item.name}`}
        tabIndex={-1}
      >
        <FoodImage src={item.imageUrl} alt={item.name} className="h-full w-full" />
      </button>
    </article>
  );
}
