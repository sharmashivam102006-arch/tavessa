import { cn } from "@/lib/utils";

/** The familiar Indian veg / non-veg square indicator. */
export function VegBadge({ isVeg, className }: { isVeg: boolean; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-[3px] border-2",
        isVeg ? "border-green-700" : "border-red-700",
        className,
      )}
      title={isVeg ? "Vegetarian" : "Non-vegetarian"}
      aria-label={isVeg ? "Vegetarian" : "Non-vegetarian"}
    >
      <span
        className={cn(
          "h-2 w-2 rounded-full",
          isVeg ? "bg-green-700" : "bg-red-700",
        )}
      />
    </span>
  );
}
