import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft } from "lucide-react";

import { ApiError, createIdempotencyKey } from "@/shared/api/client";
import { formatINR } from "@/shared/utils/money";
import { placeOrder } from "@/features/guest/api/guestApi";
import { useCartStore } from "@/features/guest/stores/cartStore";
import { findMenuItem } from "@/features/guest/data/zaika-menu";
import { priceForConfiguration } from "@/features/guest/lib/pricing";
import { BackendNotConnected, EmptyState } from "@/features/guest/components/states";
import { Button } from "@/components/ui/button";

/** Order review — the last look before placing. The Place Order
 * button generates one idempotency key per attempt and calls the real
 * POST /api/v1/orders. If the backend is not there yet, the page
 * shows the not-connected state and KEEPS the cart — it never
 * renders a fake confirmation. */
export function OrderReviewPage() {
  const { tableToken = "" } = useParams<{ tableToken: string }>();
  const navigate = useNavigate();
  const lines = useCartStore((s) => s.lines);
  const clear = useCartStore((s) => s.clear);
  const estimatedSubtotal = useCartStore((s) => s.estimatedSubtotal(findMenuItem));
  const [placing, setPlacing] = useState(false);
  const [placeError, setPlaceError] = useState<ApiError | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);

  if (lines.length === 0) {
    return (
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Review your order</h1>
        <div className="mt-4">
          <EmptyState
            title="Nothing to review"
            body="Your cart is empty. Add dishes from the menu first."
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

  const handlePlaceOrder = async () => {
    const key = createIdempotencyKey();
    setIdempotencyKey(key);
    setPlacing(true);
    setPlaceError(null);
    try {
      const response = await placeOrder(
        tableToken,
        {
          items: lines.map((line) => ({
            menuItemId: line.menuItemId,
            variantId: line.variantId,
            addOnIds: line.addOnIds,
            quantity: line.quantity,
            specialInstructions: line.specialInstructions,
          })),
        },
        key,
      );
      clear();
      navigate(`/t/${tableToken}/confirmation`, {
        state: { order: response.order },
      });
    } catch (error) {
      setPlaceError(error instanceof ApiError ? error : new ApiError(0, "request_failed", "Could not reach the backend."));
    } finally {
      setPlacing(false);
    }
  };

  return (
    <div>
      <Button variant="ghost" size="sm" asChild className="mb-2 -ml-2">
        <Link to={`/t/${tableToken}/cart`}>
          <ArrowLeft className="h-4 w-4" />
          Back to cart
        </Link>
      </Button>
      <h1 className="text-2xl font-semibold tracking-tight">Review your order</h1>
      <p className="mt-1 text-sm text-stone-600">
        Check everything below. Prices are estimated — the backend
        recalculates the final amount from the menu when the order is placed.
      </p>

      <ul className="mt-4 space-y-3">
        {lines.map((line) => {
          const item = findMenuItem(line.menuItemId);
          if (!item) return null;
          const unit = priceForConfiguration(item, line.variantId, line.addOnIds);
          const variant = line.variantId
            ? item.variants?.find((v) => v.id === line.variantId)?.name
            : undefined;
          const addOnNames = line.addOnIds
            .map((id) => item.addOns?.find((a) => a.id === id)?.name)
            .filter((n): n is string => Boolean(n));
          return (
            <li
              key={line.key}
              className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm"
            >
              <div className="flex justify-between gap-2">
                <p className="font-semibold">
                  {line.quantity} × {item.name}
                </p>
                <p className="shrink-0 font-semibold tabular-nums">{formatINR(unit * line.quantity)}</p>
              </div>
              {[variant, ...addOnNames].filter(Boolean).length > 0 ? (
                <p className="mt-0.5 text-xs text-stone-500">
                  {[variant, ...addOnNames].filter(Boolean).join(" · ")}
                </p>
              ) : null}
              {line.specialInstructions ? (
                <p className="mt-0.5 text-xs italic text-stone-500">“{line.specialInstructions}”</p>
              ) : null}
            </li>
          );
        })}
      </ul>

      <div className="mt-5 rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
        <dl className="space-y-1.5 text-sm">
          <div className="flex justify-between">
            <dt>Subtotal (estimated)</dt>
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
      </div>

      {placeError ? (
        <div className="mt-4">
          <BackendNotConnected
            endpoint="POST /api/v1/orders"
            stage="Stage 3 (backend APIs & database integration)"
            what="Placing this order"
          >
            <p className="mt-2 text-sm text-amber-800">
              The backend answered {placeError.status} ({placeError.code}). Your
              cart is saved — nothing was lost. Try again once the backend is
              connected.
              {idempotencyKey ? (
                <>
                  {" "}Your attempt's idempotency key was{" "}
                  <code className="rounded bg-amber-100 px-1 py-0.5 text-xs">{idempotencyKey}</code>;
                  retrying with the same key will not create a duplicate order.
                </>
              ) : null}
            </p>
            <Button variant="outline" size="sm" className="mt-3" onClick={handlePlaceOrder} disabled={placing}>
              Retry placing the order
            </Button>
          </BackendNotConnected>
        </div>
      ) : (
        <Button size="lg" className="mt-5 w-full" onClick={handlePlaceOrder} disabled={placing}>
          {placing ? "Placing your order…" : `Place order · ${formatINR(estimatedSubtotal)}`}
        </Button>
      )}
      <p className="mt-3 text-center text-xs text-stone-500">
        Your order goes straight to Zaika's kitchen. You can track it right
        after it is placed.
      </p>
    </div>
  );
}
