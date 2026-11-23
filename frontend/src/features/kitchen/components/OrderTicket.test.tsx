// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

// Vitest here runs without globals, so Testing Library's
// auto-cleanup does not register itself — clean up explicitly.
afterEach(() => cleanup());

import { ApiError } from "@/shared/api/client";
import type { KitchenOrder } from "@/shared/api/types";
import { KitchenColumn } from "@/features/kitchen/components/KitchenColumn";
import { OrderTicket } from "@/features/kitchen/components/OrderTicket";
import { patchKitchenOrderStatus } from "@/features/kitchen/api/kitchenApi";

vi.mock("@/features/kitchen/api/kitchenApi", () => ({
  patchKitchenOrderStatus: vi.fn(),
}));

const mockedPatch = vi.mocked(patchKitchenOrderStatus);

function ticket(overrides: Partial<KitchenOrder> = {}): KitchenOrder {
  return {
    id: "order-1",
    orderNumber: "ZAIKA-0001",
    tableSessionId: "session-1",
    status: "placed",
    items: [
      {
        id: "item-1",
        menuItemId: "paneer-tikka",
        name: "Paneer Tikka Angara",
        variantName: "Angara hot",
        addOnNames: ["Extra mint chutney"],
        quantity: 2,
        unitPricePaise: 26900,
        totalPaise: 53800,
        specialInstructions: "less spicy",
        status: "queued",
      },
    ],
    subtotalPaise: 53800,
    taxPaise: 0,
    totalPaise: 53800,
    placedAt: new Date(Date.now() - 4 * 60_000).toISOString(),
    tableNumber: 5,
    elapsedSeconds: 240,
    ...overrides,
  };
}

function renderTicket(order: KitchenOrder, restaurantId = "rest-1") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OrderTicket order={order} restaurantId={restaurantId} />
    </QueryClientProvider>,
  );
}

describe("OrderTicket", () => {
  it("shows the order number, table, items and instructions", () => {
    renderTicket(ticket());
    expect(screen.getByText("ZAIKA-0001")).toBeInTheDocument();
    expect(screen.getByText("Table 5")).toBeInTheDocument();
    expect(screen.getByText("Paneer Tikka Angara")).toBeInTheDocument();
    expect(screen.getByText("2×")).toBeInTheDocument();
    expect(screen.getByText("Angara hot")).toBeInTheDocument();
    expect(screen.getByText("+ Extra mint chutney")).toBeInTheDocument();
    expect(screen.getByText("Note: less spicy")).toBeInTheDocument();
    // Placed 4 minutes ago; the tick is derived from placedAt, so the
    // text is 04:00 or 04:01 depending on the seconds since render.
    expect(screen.getByText(/^04:0[01]$/)).toBeInTheDocument();
  });

  it("shows a DEMO badge for demo tickets", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <OrderTicket order={ticket()} restaurantId="demo" demo />
      </QueryClientProvider>,
    );
    expect(screen.getByText("DEMO")).toBeInTheDocument();
  });

  it("calls the transition with the right status on Start Preparing", async () => {
    const user = userEvent.setup();
    const order = ticket();
    mockedPatch.mockResolvedValueOnce({ data: { ...order, status: "preparing" } });
    renderTicket(order);
    await user.click(screen.getByRole("button", { name: "Start Preparing" }));
    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith("order-1", "rest-1", "preparing"),
    );
  });

  it("offers Mark Ready for a preparing order", async () => {
    const user = userEvent.setup();
    const order = ticket({ status: "preparing" });
    mockedPatch.mockResolvedValueOnce({ data: { ...order, status: "ready" } });
    renderTicket(order);
    await user.click(screen.getByRole("button", { name: "Mark Ready" }));
    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith("order-1", "rest-1", "ready"),
    );
  });

  it("has no action button on a ready ticket", () => {
    renderTicket(ticket({ status: "ready" }));
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Ready — awaiting service")).toBeInTheDocument();
  });

  it("shows the error on the ticket and stays put when the transition fails", async () => {
    const user = userEvent.setup();
    mockedPatch.mockRejectedValueOnce(
      new ApiError(409, "conflict", "Cannot move an order from placed to ready."),
    );
    renderTicket(ticket());
    const button = screen.getByRole("button", { name: "Start Preparing" });
    await user.click(button);
    await waitFor(() =>
      expect(
        screen.getByText("Cannot move an order from placed to ready."),
      ).toBeInTheDocument(),
    );
    // The ticket did not move: same order number, same button, re-enabled.
    expect(screen.getByText("ZAIKA-0001")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start Preparing" }),
    ).not.toBeDisabled();
  });
});

describe("KitchenColumn", () => {
  it("shows the count and an empty note when there are no orders", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <KitchenColumn
          title="New"
          orders={[]}
          restaurantId="rest-1"
        />
      </QueryClientProvider>,
    );
    expect(screen.getByText("New")).toBeInTheDocument();
    expect(screen.getByText("No new orders right now.")).toBeInTheDocument();
  });
});
