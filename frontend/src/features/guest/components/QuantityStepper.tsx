import { Minus, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";

/** Quantity stepper. Quantity is clamped to 1–20 by the caller
 * (the cart store clamps to 0–20; 0 removes the line). */
export function QuantityStepper({
  quantity,
  onChange,
  small = false,
  disabled = false,
}: {
  quantity: number;
  onChange: (next: number) => void;
  small?: boolean;
  disabled?: boolean;
}) {
  return (
    <div
      className={`inline-flex items-center rounded-full border border-brand-600 bg-white ${small ? "h-8" : "h-10"}`}
      role="group"
      aria-label="Quantity"
    >
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-full w-9 rounded-full px-0 text-brand-700 hover:bg-brand-50"
        onClick={() => onChange(quantity - 1)}
        disabled={disabled || quantity <= 1}
        aria-label="Decrease quantity"
      >
        <Minus className="h-4 w-4" />
      </Button>
      <span className="w-6 text-center text-sm font-semibold tabular-nums" aria-live="polite">
        {quantity}
      </span>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-full w-9 rounded-full px-0 text-brand-700 hover:bg-brand-50"
        onClick={() => onChange(quantity + 1)}
        disabled={disabled || quantity >= 20}
        aria-label="Increase quantity"
      >
        <Plus className="h-4 w-4" />
      </Button>
    </div>
  );
}
