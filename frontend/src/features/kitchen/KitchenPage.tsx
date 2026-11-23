/** Kitchen Display System — the KDS board (Stage 4).
 *
 * Three columns (New / Preparing / Ready) fed by the active queue
 * via REST; a WebSocket invalidates the queue on connect, reconnect
 * and every order event — the socket never writes state directly.
 * WS failure does not break the board: it keeps showing the last
 * REST snapshot, with a Refresh button and a reconnecting banner.
 *
 * `?demo=1` shows clearly labelled sample tickets from
 * demo-tickets.ts — never mixed with live data, never by default.
 *
 * States: loading skeleton, empty ("No active orders…"), error with
 * Retry, and a "kitchen access not configured" state when
 * VITE_KITCHEN_DEV_KEY is missing (never a stack trace).
 */
import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router";

import { ApiError } from "@/shared/api/client";
import {
  KITCHEN_COLUMNS,
  type KitchenOrder,
  type KitchenRestaurant,
} from "@/shared/api/types";
import { isKitchenConfigured } from "@/features/kitchen/api/kitchenApi";
import { KitchenColumn } from "@/features/kitchen/components/KitchenColumn";
import { DEMO_TICKETS } from "@/features/kitchen/data/demo-tickets";
import {
  useKitchenOrders,
  useKitchenRestaurants,
  useKitchenSocket,
} from "@/features/kitchen/hooks/useKitchen";
import { columnForStatus } from "@/features/kitchen/lib/kitchen";
import { useKitchenUiStore } from "@/features/kitchen/stores/kitchenUiStore";

/** Stable empty list so the restaurantId memo below does not see a
 * new array identity on every render. */
const EMPTY_RESTAURANTS: KitchenRestaurant[] = [];

function groupByColumn(orders: KitchenOrder[]) {
  const grouped: Record<string, KitchenOrder[]> = {
    new: [],
    preparing: [],
    ready: [],
  };
  for (const order of orders) {
    const column = columnForStatus(order.status);
    if (column) grouped[column].push(order);
  }
  return grouped;
}

function LoadingSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      {KITCHEN_COLUMNS.map((column) => (
        <section key={column.id} aria-label={`${column.title} loading`}>
          <div className="h-6 w-32 animate-pulse rounded bg-stone-300" />
          <div className="mt-3 space-y-3">
            {[0, 1].map((i) => (
              <div
                key={i}
                className="h-44 animate-pulse rounded-xl border border-stone-200 bg-white"
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export function KitchenPage() {
  const [searchParams] = useSearchParams();
  const demo = searchParams.get("demo") === "1";
  const setConnection = useKitchenUiStore((s) => s.setConnection);
  const setRestaurantName = useKitchenUiStore((s) => s.setRestaurantName);

  const configured = isKitchenConfigured();
  const restaurantsQuery = useKitchenRestaurants();
  const restaurants = restaurantsQuery.data?.data ?? EMPTY_RESTAURANTS;
  // `?restaurant=<id>` picks explicitly; otherwise default to the
  // only restaurant when there is exactly one.
  const restaurantParam = searchParams.get("restaurant");
  const restaurantId = useMemo(() => {
    if (
      restaurantParam &&
      restaurants.some((r) => r.id === restaurantParam)
    ) {
      return restaurantParam;
    }
    if (restaurants.length === 1) return restaurants[0].id;
    return undefined;
  }, [restaurants, restaurantParam]);
  const restaurantName = restaurants.find((r) => r.id === restaurantId)?.name;

  const ordersQuery = useKitchenOrders(demo ? undefined : restaurantId);
  const { connection } = useKitchenSocket(demo ? undefined : restaurantId);

  useEffect(() => {
    setConnection(demo ? "offline" : connection);
  }, [connection, demo, setConnection]);
  useEffect(() => {
    setRestaurantName(demo ? "Zaika · Demo" : restaurantName);
  }, [restaurantName, demo, setRestaurantName]);

  if (demo) {
    const grouped = groupByColumn(DEMO_TICKETS);
    return (
      <div>
        <div
          className="rounded-lg bg-amber-100 px-4 py-3 text-sm font-semibold text-amber-900"
          role="note"
        >
          DEMO — sample tickets, not live orders. Add ?demo=1 to preview the
          board; remove it to see the live queue.
        </div>
        <div className="mt-6 grid gap-6 lg:grid-cols-3">
          {KITCHEN_COLUMNS.map((column) => (
            <KitchenColumn
              key={column.id}
              title={column.title}
              orders={grouped[column.id]}
              restaurantId="demo"
              demo
            />
          ))}
        </div>
      </div>
    );
  }

  if (!configured) {
    return (
      <div className="mx-auto max-w-xl rounded-xl border border-amber-300 bg-amber-50 p-6">
        <h1 className="text-lg font-semibold text-amber-900">
          Kitchen access not configured
        </h1>
        <p className="mt-2 text-sm text-amber-900">
          The Kitchen Display needs a dev key. Set{" "}
          <code className="rounded bg-amber-100 px-1 py-0.5">
            VITE_KITCHEN_DEV_KEY
          </code>{" "}
          in <code className="rounded bg-amber-100 px-1 py-0.5">frontend/.env</code>{" "}
          (it must match the backend&apos;s{" "}
          <code className="rounded bg-amber-100 px-1 py-0.5">KITCHEN_DEV_KEY</code>)
          and restart the dev server. The KDS is development-access only
          until staff auth lands.
        </p>
      </div>
    );
  }

  if (restaurantsQuery.isPending) return <LoadingSkeleton />;
  if (restaurantsQuery.isError) {
    return (
      <div className="mx-auto max-w-xl rounded-xl border border-red-200 bg-red-50 p-6">
        <h1 className="text-lg font-semibold text-red-900">
          Could not load restaurants
        </h1>
        <p className="mt-2 text-sm text-red-800">
          {restaurantsQuery.error instanceof ApiError
            ? restaurantsQuery.error.message
            : "The kitchen service could not be reached."}
        </p>
        <button
          type="button"
          onClick={() => void restaurantsQuery.refetch()}
          className="mt-4 rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white"
        >
          Retry
        </button>
      </div>
    );
  }
  if (restaurants.length === 0) {
    return (
      <div className="mx-auto max-w-xl rounded-xl border border-stone-200 bg-white p-6">
        <h1 className="text-lg font-semibold">No restaurants found</h1>
        <p className="mt-2 text-sm text-stone-600">
          The backend returned no restaurants. Seed Zaika and try again.
        </p>
      </div>
    );
  }
  if (!restaurantId) {
    return (
      <div className="mx-auto max-w-xl rounded-xl border border-stone-200 bg-white p-6">
        <h1 className="text-lg font-semibold">Choose a restaurant</h1>
        <p className="mt-2 text-sm text-stone-600">
          More than one restaurant exists — pick the one this screen serves.
        </p>
        <ul className="mt-4 space-y-2">
          {restaurants.map((restaurant) => (
            <li key={restaurant.id}>
              <a
                className="block rounded-lg border border-stone-200 px-4 py-3 font-medium hover:bg-stone-50"
                href={`/kitchen?restaurant=${restaurant.id}`}
              >
                {restaurant.name}
              </a>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const orders = ordersQuery.data?.data ?? [];
  const grouped = groupByColumn(orders);

  return (
    <div>
      {(connection === "reconnecting" || connection === "offline") &&
      !ordersQuery.isPending ? (
        <div
          className="rounded-lg bg-amber-100 px-4 py-3 text-sm font-medium text-amber-900"
          role="status"
        >
          Connection lost — showing the last loaded orders. Reconnecting…
          Use Refresh to reload now.
        </div>
      ) : null}

      {ordersQuery.isPending ? (
        <div className="mt-6">
          <LoadingSkeleton />
        </div>
      ) : ordersQuery.isError ? (
        <div className="mx-auto mt-6 max-w-xl rounded-xl border border-red-200 bg-red-50 p-6">
          <h1 className="text-lg font-semibold text-red-900">
            Could not load the kitchen queue
          </h1>
          <p className="mt-2 text-sm text-red-800">
            {ordersQuery.error instanceof ApiError
              ? ordersQuery.error.message
              : "The kitchen service could not be reached."}
          </p>
          <button
            type="button"
            onClick={() => void ordersQuery.refetch()}
            className="mt-4 rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white"
          >
            Retry
          </button>
        </div>
      ) : orders.length === 0 ? (
        <div className="mx-auto mt-6 max-w-xl rounded-xl border border-stone-200 bg-white p-8 text-center">
          <p className="text-lg font-semibold">
            No active orders — new guest orders will appear here
          </p>
          <p className="mt-2 text-sm text-stone-600">
            Orders placed from a guest phone show up on this board without a
            manual refresh.
          </p>
        </div>
      ) : (
        <div className="mt-6 grid gap-6 lg:grid-cols-3">
          {KITCHEN_COLUMNS.map((column) => (
            <KitchenColumn
              key={column.id}
              title={column.title}
              orders={grouped[column.id]}
              restaurantId={restaurantId}
            />
          ))}
        </div>
      )}
    </div>
  );
}
