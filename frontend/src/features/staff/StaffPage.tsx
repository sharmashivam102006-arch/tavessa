/** Waiter / Staff dashboard (Stage 5).
 *
 * Floor grid (live table state), table detail on selection, active
 * orders with "Ready to serve" first, and the help-call queue —
 * all fed by REST; a WebSocket invalidates the queries on connect,
 * reconnect and every staff event — the socket never writes state
 * directly. WS failure does not break the board: it keeps showing
 * the last REST snapshot, with a Refresh button and a reconnecting
 * banner.
 *
 * `?demo=1` shows clearly labelled sample data from demo-staff.ts
 * — never mixed with live data, never by default.
 *
 * States: loading skeleton, empty ("No active orders…"), error
 * with Retry, and a "staff access not configured" state when
 * VITE_STAFF_DEV_KEY is missing (never a stack trace).
 *
 * NOT in this stage (brief § 6): no bill requests, no session
 * close, no table-status transitions, no staff assignments, no
 * payroll/scheduling, no production login — see the roadmap
 * Stage 5 report for where each deferred item belongs.
 */
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { ApiError } from "@/shared/api/client";
import type {
  StaffRestaurant,
  StaffTableDetail,
} from "@/shared/api/types";
import { isStaffConfigured } from "@/features/staff/api/staffApi";
import { ActiveOrders } from "@/features/staff/components/ActiveOrders";
import { FloorGrid } from "@/features/staff/components/FloorGrid";
import { HelpCallQueue } from "@/features/staff/components/HelpCallQueue";
import { TableDetail } from "@/features/staff/components/TableDetail";
import {
  DEMO_HELP_CALLS,
  DEMO_ORDERS,
  DEMO_TABLES,
} from "@/features/staff/data/demo-staff";
import {
  useStaffHelpCalls,
  useStaffOrders,
  useStaffRestaurants,
  useStaffSocket,
  useStaffTableDetail,
  useStaffTables,
} from "@/features/staff/hooks/useStaff";
import { useStaffUiStore } from "@/features/staff/stores/staffUiStore";

/** Stable empty list so the restaurantId memo below does not see a
 * new array identity on every render. */
const EMPTY_RESTAURANTS: StaffRestaurant[] = [];

function LoadingSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
        {Array.from({ length: 10 }, (_, i) => (
          <div
            key={i}
            className="h-28 animate-pulse rounded-xl border border-stone-200 bg-white"
          />
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="h-64 animate-pulse rounded-xl border border-stone-200 bg-white lg:col-span-2" />
        <div className="h-64 animate-pulse rounded-xl border border-stone-200 bg-white" />
      </div>
    </div>
  );
}

/** The demo table detail, derived from the demo data (same shape as
 * the live GET /staff/tables/:id response). */
function demoTableDetail(tableId: string): StaffTableDetail | undefined {
  const table = DEMO_TABLES.find((t) => t.id === tableId);
  if (!table) return undefined;
  return {
    table,
    session: table.sessionId
      ? {
          id: table.sessionId,
          restaurantId: "demo",
          tableId: table.id,
          tableNumber: table.number,
          status: "active",
          openedAt: table.sessionOpenedAt ?? new Date().toISOString(),
        }
      : null,
    orders: DEMO_ORDERS.filter((o) => o.tableSessionId === table.sessionId),
    helpCalls: DEMO_HELP_CALLS.filter(
      (c) => c.tableSessionId === table.sessionId,
    ),
  };
}

export function StaffPage() {
  const [searchParams] = useSearchParams();
  const demo = searchParams.get("demo") === "1";
  const setConnection = useStaffUiStore((s) => s.setConnection);
  const setRestaurantName = useStaffUiStore((s) => s.setRestaurantName);
  const [selectedTableId, setSelectedTableId] = useState<string | undefined>(
    undefined,
  );

  const configured = isStaffConfigured();
  const restaurantsQuery = useStaffRestaurants();
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

  const tablesQuery = useStaffTables(demo ? undefined : restaurantId);
  const ordersQuery = useStaffOrders(demo ? undefined : restaurantId);
  const helpCallsQuery = useStaffHelpCalls(demo ? undefined : restaurantId);
  const detailQuery = useStaffTableDetail(
    demo ? undefined : restaurantId,
    selectedTableId,
  );
  const { connection } = useStaffSocket(demo ? undefined : restaurantId);

  useEffect(() => {
    setConnection(demo ? "offline" : connection);
  }, [connection, demo, setConnection]);
  useEffect(() => {
    setRestaurantName(demo ? "Zaika · Demo" : restaurantName);
  }, [restaurantName, demo, setRestaurantName]);

  if (demo) {
    const detail = selectedTableId
      ? demoTableDetail(selectedTableId)
      : undefined;
    return (
      <div>
        <div
          className="rounded-lg bg-amber-100 px-4 py-3 text-sm font-semibold text-amber-900"
          role="note"
        >
          DEMO — sample floor, orders and help calls, not live. Add
          ?demo=1 to preview the board; remove it to see the live
          floor. Actions are disabled in demo.
        </div>
        <section aria-label="Floor" className="mt-6">
          <h2 className="text-lg font-semibold">Floor</h2>
          <div className="mt-3">
            <FloorGrid
              tables={DEMO_TABLES}
              selectedTableId={selectedTableId}
              onSelect={(id) =>
                setSelectedTableId(id === selectedTableId ? undefined : id)
              }
            />
          </div>
        </section>
        {detail ? (
          <div className="mt-4">
            <TableDetail
              detail={detail}
              restaurantId="demo"
              demo
              onClose={() => setSelectedTableId(undefined)}
            />
          </div>
        ) : null}
        <div className="mt-6 grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <ActiveOrders orders={DEMO_ORDERS} restaurantId="demo" demo />
          </div>
          <HelpCallQueue
            calls={DEMO_HELP_CALLS}
            restaurantId="demo"
            demo
          />
        </div>
      </div>
    );
  }

  if (!configured) {
    return (
      <div className="mx-auto max-w-xl rounded-xl border border-amber-300 bg-amber-50 p-6">
        <h1 className="text-lg font-semibold text-amber-900">
          Staff access not configured
        </h1>
        <p className="mt-2 text-sm text-amber-900">
          The staff dashboard needs a dev key. Set{" "}
          <code className="rounded bg-amber-100 px-1 py-0.5">
            VITE_STAFF_DEV_KEY
          </code>{" "}
          in <code className="rounded bg-amber-100 px-1 py-0.5">frontend/.env</code>{" "}
          (it must match the backend&apos;s{" "}
          <code className="rounded bg-amber-100 px-1 py-0.5">STAFF_DEV_KEY</code>)
          and restart the dev server. The staff board is
          development-access only until staff auth lands.
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
            : "The staff service could not be reached."}
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
                href={`/staff?restaurant=${restaurant.id}`}
              >
                {restaurant.name}
              </a>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  const tables = tablesQuery.data?.data ?? [];
  const orders = ordersQuery.data?.data ?? [];
  const helpCalls = helpCallsQuery.data?.data ?? [];
  const loading =
    tablesQuery.isPending || ordersQuery.isPending || helpCallsQuery.isPending;
  const queryError =
    tablesQuery.error ?? ordersQuery.error ?? helpCallsQuery.error;

  return (
    <div>
      {(connection === "reconnecting" || connection === "offline") &&
      !loading ? (
        <div
          className="rounded-lg bg-amber-100 px-4 py-3 text-sm font-medium text-amber-900"
          role="status"
        >
          Connection lost — showing the last loaded floor. Reconnecting…
          Use Refresh to reload now.
        </div>
      ) : null}

      {loading ? (
        <div className="mt-6">
          <LoadingSkeleton />
        </div>
      ) : queryError ? (
        <div className="mx-auto mt-6 max-w-xl rounded-xl border border-red-200 bg-red-50 p-6">
          <h1 className="text-lg font-semibold text-red-900">
            Could not load the staff board
          </h1>
          <p className="mt-2 text-sm text-red-800">
            {queryError instanceof ApiError
              ? queryError.message
              : "The staff service could not be reached."}
          </p>
          <button
            type="button"
            onClick={() => {
              void tablesQuery.refetch();
              void ordersQuery.refetch();
              void helpCallsQuery.refetch();
            }}
            className="mt-4 rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white"
          >
            Retry
          </button>
        </div>
      ) : (
        <div className="mt-6">
          <section aria-label="Floor">
            <h2 className="text-lg font-semibold">Floor</h2>
            {tables.length === 0 ? (
              <p className="mt-3 rounded-xl border border-stone-200 bg-white p-6 text-sm text-stone-600">
                No tables found for this restaurant.
              </p>
            ) : (
              <div className="mt-3">
                <FloorGrid
                  tables={tables}
                  selectedTableId={selectedTableId}
                  onSelect={(id) =>
                    setSelectedTableId(id === selectedTableId ? undefined : id)
                  }
                />
              </div>
            )}
          </section>

          {selectedTableId && detailQuery.data ? (
            <TableDetail
              detail={detailQuery.data.data}
              restaurantId={restaurantId}
              onClose={() => setSelectedTableId(undefined)}
            />
          ) : null}

          <div className="mt-6 grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <ActiveOrders orders={orders} restaurantId={restaurantId} />
            </div>
            <HelpCallQueue calls={helpCalls} restaurantId={restaurantId} />
          </div>
        </div>
      )}
    </div>
  );
}
