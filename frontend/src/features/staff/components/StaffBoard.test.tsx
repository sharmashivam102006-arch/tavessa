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
import type { KitchenOrder, StaffHelpCall, StaffTable } from "@/shared/api/types";
import { FloorGrid } from "@/features/staff/components/FloorGrid";
import { StaffOrderCard } from "@/features/staff/components/StaffOrderCard";
import { HelpCallQueue } from "@/features/staff/components/HelpCallQueue";
import { ActiveOrders } from "@/features/staff/components/ActiveOrders";
import {
  acknowledgeHelpCall,
  resolveHelpCall,
  serveStaffOrder,
} from "@/features/staff/api/staffApi";

vi.mock("@/features/staff/api/staffApi", () => ({
  acknowledgeHelpCall: vi.fn(),
  resolveHelpCall: vi.fn(),
  serveStaffOrder: vi.fn(),
}));

const mockedServe = vi.mocked(serveStaffOrder);
const mockedAck = vi.mocked(acknowledgeHelpCall);
const mockedResolve = vi.mocked(resolveHelpCall);

function order(overrides: Partial<KitchenOrder> = {}): KitchenOrder {
  return {
    id: "order-1",
    orderNumber: "ZAIKA-0001",
    tableSessionId: "session-1",
    status: "ready",
    items: [
      {
        id: "item-1",
        menuItemId: "butter-naan",
        name: "Butter Naan",
        quantity: 2,
        unitPricePaise: 4900,
        totalPaise: 9800,
        specialInstructions: "extra butter",
        status: "ready",
      },
    ],
    subtotalPaise: 9800,
    taxPaise: 0,
    totalPaise: 9800,
    placedAt: new Date(Date.now() - 4 * 60_000).toISOString(),
    tableNumber: 5,
    elapsedSeconds: 240,
    ...overrides,
  };
}

function table(overrides: Partial<StaffTable> = {}): StaffTable {
  return {
    id: "table-1",
    number: 5,
    status: "occupied",
    sessionId: "session-1",
    sessionOpenedAt: new Date().toISOString(),
    activeOrderCount: 1,
    readyOrderCount: 1,
    latestOrderStatus: "ready",
    hasOpenHelpCall: true,
    ...overrides,
  };
}

function helpCall(overrides: Partial<StaffHelpCall> = {}): StaffHelpCall {
  return {
    id: "call-1",
    tableSessionId: "session-1",
    type: "water",
    note: "Still water, please",
    status: "open",
    createdAt: new Date(Date.now() - 2 * 60_000).toISOString(),
    tableNumber: 5,
    acknowledgedAt: null,
    resolvedAt: null,
    ...overrides,
  };
}

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("FloorGrid", () => {
  it("renders tables with status and live badges", () => {
    const onSelect = vi.fn();
    renderWithQuery(
      <FloorGrid
        tables={[
          table(),
          table({
            id: "table-2",
            number: 1,
            status: "available",
            sessionId: null,
            sessionOpenedAt: null,
            activeOrderCount: 0,
            readyOrderCount: 0,
            latestOrderStatus: null,
            hasOpenHelpCall: false,
          }),
        ]}
        selectedTableId={undefined}
        onSelect={onSelect}
      />,
    );
    expect(screen.getByRole("button", { name: /Table 5, Occupied/ })).toBeInTheDocument();
    expect(screen.getByText("READY")).toBeInTheDocument();
    expect(screen.getByText("NEEDS HELP")).toBeInTheDocument();
    expect(screen.getByText("1 order · Ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Table 1, Available/ })).toBeInTheDocument();
    expect(screen.getByText("No orders")).toBeInTheDocument();
  });

  it("selects a table on click and marks it pressed", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderWithQuery(
      <FloorGrid
        tables={[table()]}
        selectedTableId="table-1"
        onSelect={onSelect}
      />,
    );
    const button = screen.getByRole("button", { name: /Table 5/ });
    expect(button).toHaveAttribute("aria-pressed", "true");
    await user.click(button);
    expect(onSelect).toHaveBeenCalledWith("table-1");
  });
});

describe("ActiveOrders", () => {
  it("renders the ready section before the in-progress section", () => {
    renderWithQuery(
      <ActiveOrders
        orders={[
          order({ id: "placed-1", status: "placed", orderNumber: "ZAIKA-0002" }),
          order({ id: "ready-1", status: "ready", orderNumber: "ZAIKA-0001" }),
        ]}
        restaurantId="rest-1"
      />,
    );
    const headings = screen.getAllByRole("heading");
    const readyIndex = headings.findIndex((h) =>
      h.textContent?.includes("Ready to serve"),
    );
    const progressIndex = headings.findIndex((h) =>
      h.textContent?.includes("In progress"),
    );
    expect(readyIndex).toBeGreaterThanOrEqual(0);
    expect(progressIndex).toBeGreaterThan(readyIndex);
    // The ready card comes before the placed card in the DOM.
    const cards = screen.getAllByRole("article");
    expect(cards[0]).toHaveTextContent("ZAIKA-0001");
    expect(cards[1]).toHaveTextContent("ZAIKA-0002");
  });

  it("shows the empty state when there are no active orders", () => {
    renderWithQuery(<ActiveOrders orders={[]} restaurantId="rest-1" />);
    expect(
      screen.getByText("No active orders — new guest orders will appear here."),
    ).toBeInTheDocument();
  });
});

describe("StaffOrderCard + ServeConfirm", () => {
  it("shows the order, items, instructions and the Serve button for a ready order", () => {
    renderWithQuery(
      <StaffOrderCard order={order()} restaurantId="rest-1" />,
    );
    expect(screen.getByText("ZAIKA-0001")).toBeInTheDocument();
    expect(screen.getByText("Table 5")).toBeInTheDocument();
    expect(screen.getByText("Butter Naan")).toBeInTheDocument();
    expect(screen.getByText("Note: extra butter")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Serve" })).toBeInTheDocument();
  });

  it("has no serve action on a non-ready order", () => {
    renderWithQuery(
      <StaffOrderCard
        order={order({ status: "preparing" })}
        restaurantId="rest-1"
      />,
    );
    expect(screen.queryByRole("button", { name: "Serve" })).toBeNull();
    expect(
      screen.getByText("Cooking — serving opens once it is ready"),
    ).toBeInTheDocument();
  });

  it("asks for confirmation, then serves on confirm", async () => {
    const user = userEvent.setup();
    mockedServe.mockResolvedValueOnce({
      data: { ...order(), status: "served" },
    });
    renderWithQuery(
      <StaffOrderCard order={order()} restaurantId="rest-1" />,
    );
    await user.click(screen.getByRole("button", { name: "Serve" }));
    expect(
      screen.getByText("Mark this order served?"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm serve" }));
    await waitFor(() =>
      expect(mockedServe).toHaveBeenCalledWith("order-1", "rest-1"),
    );
  });

  it("cancels the confirmation without calling the API", async () => {
    const user = userEvent.setup();
    renderWithQuery(
      <StaffOrderCard order={order()} restaurantId="rest-1" />,
    );
    await user.click(screen.getByRole("button", { name: "Serve" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Serve" })).toBeInTheDocument();
    expect(mockedServe).not.toHaveBeenCalled();
  });

  it("shows the error on the card and leaves the order in place when serving fails", async () => {
    const user = userEvent.setup();
    mockedServe.mockRejectedValueOnce(
      new ApiError(409, "conflict", "Cannot serve an order in status placed."),
    );
    renderWithQuery(
      <StaffOrderCard order={order()} restaurantId="rest-1" />,
    );
    await user.click(screen.getByRole("button", { name: "Serve" }));
    await user.click(screen.getByRole("button", { name: "Confirm serve" }));
    await waitFor(() =>
      expect(
        screen.getByText("Cannot serve an order in status placed."),
      ).toBeInTheDocument(),
    );
    // The order did not move: same number, same Serve button.
    expect(screen.getByText("ZAIKA-0001")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("disables serve in demo mode", () => {
    renderWithQuery(
      <StaffOrderCard order={order()} restaurantId="demo" demo />,
    );
    expect(
      screen.getByText("Serve — disabled in demo"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Serve" })).toBeNull();
  });
});

describe("HelpCallQueue", () => {
  it("shows the call with table, type, note and an Acknowledge button", () => {
    renderWithQuery(
      <HelpCallQueue calls={[helpCall()]} restaurantId="rest-1" />,
    );
    expect(screen.getByText("Table 5")).toBeInTheDocument();
    expect(screen.getByText("Request water")).toBeInTheDocument();
    expect(screen.getByText("Still water, please")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Acknowledge" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resolve" })).toBeNull();
  });

  it("offers Resolve only after acknowledgement", async () => {
    const user = userEvent.setup();
    mockedAck.mockResolvedValueOnce({
      data: { ...helpCall(), status: "acknowledged" },
    });
    const { rerender } = renderWithQuery(
      <HelpCallQueue calls={[helpCall()]} restaurantId="rest-1" />,
    );
    await user.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() =>
      expect(mockedAck).toHaveBeenCalledWith("call-1", "rest-1"),
    );
    // After the server confirms, the refetched call is acknowledged.
    const queryClient = new QueryClient();
    rerender(
      <QueryClientProvider client={queryClient}>
        <HelpCallQueue
          calls={[
            helpCall({
              status: "acknowledged",
              acknowledgedAt: new Date().toISOString(),
            }),
          ]}
          restaurantId="rest-1"
        />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("button", { name: "Resolve" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
  });

  it("resolves an acknowledged call", async () => {
    const user = userEvent.setup();
    mockedResolve.mockResolvedValueOnce({
      data: { ...helpCall(), status: "resolved" },
    });
    renderWithQuery(
      <HelpCallQueue
        calls={[helpCall({ status: "acknowledged" })]}
        restaurantId="rest-1"
      />,
    );
    await user.click(screen.getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(mockedResolve).toHaveBeenCalledWith("call-1", "rest-1"),
    );
  });

  it("shows the error on the call and leaves it in place when acknowledge fails", async () => {
    const user = userEvent.setup();
    mockedAck.mockRejectedValueOnce(
      new ApiError(409, "conflict", "Help call is not open."),
    );
    renderWithQuery(
      <HelpCallQueue calls={[helpCall()]} restaurantId="rest-1" />,
    );
    await user.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() =>
      expect(screen.getByText("Help call is not open.")).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Acknowledge" }),
    ).toBeInTheDocument();
  });

  it("shows the empty state when there are no unresolved calls", () => {
    renderWithQuery(<HelpCallQueue calls={[]} restaurantId="rest-1" />);
    expect(
      screen.getByText(/No open help calls/),
    ).toBeInTheDocument();
  });

  it("disables actions in demo mode", () => {
    renderWithQuery(
      <HelpCallQueue calls={[helpCall()]} restaurantId="demo" demo />,
    );
    expect(
      screen.getByText("Acknowledge / Resolve — disabled in demo"),
    ).toBeInTheDocument();
  });
});
