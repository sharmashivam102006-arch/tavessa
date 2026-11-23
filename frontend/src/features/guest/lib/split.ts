/** Bill splitting — integer paise, exact sums (pure functions).
 *
 * Equal split rule (api-plan.md § 7.1, database-design.md § 3.18):
 * the remainder paise are distributed 1p each to the earliest parts,
 * so the parts always sum exactly to the total. Custom splits are
 * valid only when every amount is > 0 and the amounts sum exactly
 * to the total. */
export function equalSplit(totalPaise: number, parts: number): number[] {
  if (!Number.isInteger(totalPaise) || totalPaise < 0) {
    throw new Error("Total must be a non-negative integer number of paise.");
  }
  if (!Number.isInteger(parts) || parts < 2) {
    throw new Error("Equal split needs at least 2 parts.");
  }
  const base = Math.floor(totalPaise / parts);
  const remainder = totalPaise % parts;
  return Array.from({ length: parts }, (_, i) => base + (i < remainder ? 1 : 0));
}

export interface SplitValidation {
  valid: boolean;
  error?: string;
}

export function validateCustomSplit(
  amountsPaise: number[],
  totalPaise: number,
): SplitValidation {
  if (amountsPaise.length < 2) {
    return { valid: false, error: "A custom split needs at least 2 parts." };
  }
  if (amountsPaise.some((a) => !Number.isInteger(a) || a <= 0)) {
    return { valid: false, error: "Each part must be a whole amount above ₹0." };
  }
  const sum = amountsPaise.reduce((acc, a) => acc + a, 0);
  if (sum !== totalPaise) {
    return {
      valid: false,
      error: `The parts add up to ${sum} paise, but the bill is ${totalPaise} paise. Adjust the amounts so they match exactly.`,
    };
  }
  return { valid: true };
}
