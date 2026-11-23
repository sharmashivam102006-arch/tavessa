import { useState } from "react";
import { UtensilsCrossed } from "lucide-react";

import { cn } from "@/lib/utils";

/** Food photo with a graceful fallback — a styled placeholder with a
 * utensil mark, never a broken-image icon. */
export function FoodImage({
  src,
  alt,
  className,
}: {
  src?: string;
  alt: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return (
      <div
        className={cn(
          "flex items-center justify-center bg-brand-100 text-brand-700",
          className,
        )}
        aria-label={alt}
        role="img"
      >
        <UtensilsCrossed className="h-8 w-8 opacity-60" />
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setFailed(true)}
      className={cn("object-cover", className)}
    />
  );
}
