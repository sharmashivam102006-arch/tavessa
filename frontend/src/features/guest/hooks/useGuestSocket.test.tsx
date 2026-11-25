// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

afterEach(() => cleanup());

import { ApiError } from "@/shared/api/client";
import type { BillStatus } from "@/shared/api/types";
import {
  fetchGuestBill,
  fetchGuestOrders,
  fetchGuestWsTicket,
} from "@/features/guest/api/guestApi";
import { useGuestSocket } from "@/features/guest/hooks/useGuestSocket";
import { useGuestBill, useGuestOrders } from "@/features/guest/hooks/useGuest";

vi.mock("@/features/guest/api/guestApi", () => ({
  fetchGuestSession: vi.fn(),
  fetchGuestSessionCurrent: vi.fn(),
  fetchGuestMenu: vi.fn(),
  placeOrder: vi.fn(),
  fetchGuestOrders: vi.fn(),
  fetchGuestOrder: vi.fn(),
  cancelOrder: vi.fn(),
  requestHelpCall: vi.fn(),
  fetchGuestHelpCalls: vi.fn(),
  fetchGuestWsTicket: vi.fn(),
  fetchGuestBill: vi.fn(),
  requestBill: vi.fn(),
  requestCashPayment: vi.fn(),
  createBillSplit: vi.fn(),
  fetchGuestReceipt: vi.fn(),
  submitReview: vi.fn(),
}));

vi.mock("@/features/guest/lib/guestSocket", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/features/guest/lib/guestSocket")>();
  return {
    ...actual,
    // Reconnects happen ~instantly in tests (real backoff is
    // covered by guestSocket.test.ts).
    nextGuestBackoffDelayMs: () => 10,
  };
});
const mockedFetchTicket = vi.mocked(fetchGuestWsTicket);
const mockedFetchOrders = vi.mocked(fetchGuestOrders);
const mockedFetchBill = vi.mocked(fetchGuestBill);

type FakeSocket = {
  url: string;
  readyState: number;
  onopen: ((e: unknown) => void) | null;
  onmessage: ((e: { data: string }) => void) | null;
  onclose: ((e: { code: number }) => void) | null;
  onerror: ((e: unknown) => void) | null;
  close: (code?: number) => void;
  open: () => void;
  receive: (event: Record<string, unknown>) => void;
  closeWith: (code: number) => void;
};

let instances: FakeSocket[] = [];

function installFakeWebSocket() {
  instances = [];
  class Fake implements FakeSocket {
    url: string;
    readyState = 0;
    onopen: ((e: unknown) => void) | null = null;
    onmessage: ((e: { data: string }) => void) | null = null;
    onclose: ((e: { code: number }) => void) | null = null;
    onerror: ((e: unknown) => void) | null = null;
    constructor(url: string) {
      this.url = url;
      instances.push(this as unknown as FakeSocket);
    }
    close(code = 1000) {
      this.readyState = 3;
      this.onclose?.({ code });
    }
    open() {
      this.readyState = 1;
      this.onopen?.({});
    }
    receive(event: Record<string, unknown>) {
      this.onmessage?.({ data: JSON.stringify(event) });
    }
    closeWith(code: number) {
      this.readyState = 3;
      this.onclose?.({ code });
    }
  }
  vi.stubGlobal("WebSocket", Fake);
}

function guestEvent(overrides: Record<string, unknown> = {}) {
  return {
    type: "order.created",
    restaurant_id: "rest-1",
    table_id: "table-1",
    data: { order_id: "order-1", order_number: "ZAIKA-0001", status: "placed", table_number: 5 },
    version: 1,
    occurred_at: new Date().toISOString(),
    ...overrides,
  };
}

function billData(status: BillStatus = "issued") {
  return {
    data: {
      id: "bill-1",
      billNumber: "B-2026-11-25-001",
      tableSessionId: "session-1",
      tableId: "table-1",
      tableNumber: 5,
      status,
      items: [],
      splits: [],
      payments: [],
      orderNumbers: ["ZAIKA-0001"],
      subtotalPaise: 9800,
      taxPaise: 0,
      serviceChargePaise: 0,
      discountPaise: 0,
      totalPaise: 9800,
      amountPaidPaise: 0,
      outstandingPaise: 9800,
      issuedAt: new Date().toISOString(),
      version: 1,
    },
  };
}

function Probe({
  tableToken,
  onEvent,
}: {
  tableToken: string;
  onEvent?: (event: unknown) => void;
}) {
  const { connection } = useGuestSocket(tableToken, { onEvent });
  const orders = useGuestOrders(tableToken);
  const bill = useGuestBill(tableToken);
  return (
    <div>
      <p data-testid="connection">{connection}</p>
      <p data-testid="orders">{orders.data ? "orders-loaded" : "orders-pending"}</p>
      <p data-testid="bill-status">{bill.data?.data?.status ?? "bill-pending"}</p>
    </div>
  );
}

function renderProbe(tableToken = "token-abc", onEvent?: (event: unknown) => void) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <Probe tableToken={tableToken} onEvent={onEvent} />
    </QueryClientProvider>,
  );
  return { ...utils, queryClient };
}

beforeEach(() => {
  installFakeWebSocket();
  mockedFetchTicket.mockResolvedValue({
    data: { ticket: "ticket-1", channel: "guest", expiresAt: "2099-01-01T00:00:00Z" },
  });
  mockedFetchOrders.mockResolvedValue({ data: [] });
  mockedFetchBill.mockResolvedValue(billData());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("useGuestSocket", () => {
  it("acquires a ticket, connects once, and reports live", async () => {
    renderProbe();
    await waitFor(() => {
      expect(mockedFetchTicket).toHaveBeenCalledWith("token-abc");
    });
    expect(instances).toHaveLength(1);
    expect(instances[0].url).toContain("/ws/guest?ticket=ticket-1");
    instances[0].open();
    await waitFor(() => {
      expect(screen.getByTestId("connection")).toHaveTextContent("live");
    });
  });

  it("does not open a socket for the demo token", async () => {
    renderProbe("demo");
    await new Promise((r) => setTimeout(r, 50));
    expect(mockedFetchTicket).not.toHaveBeenCalled();
    expect(instances).toHaveLength(0);
    expect(screen.getByTestId("connection")).toHaveTextContent("offline");
  });

  it("invalidates guest queries on connect and on events", async () => {
    const onEvent = vi.fn();
    renderProbe("token-abc", onEvent);
    await waitFor(() => expect(instances).toHaveLength(1));
    instances[0].open();
    await waitFor(() => {
      expect(screen.getByTestId("connection")).toHaveTextContent("live");
    });
    const ordersCallsAfterOpen = mockedFetchOrders.mock.calls.length;
    expect(ordersCallsAfterOpen).toBeGreaterThanOrEqual(1);
    instances[0].receive(guestEvent({ version: 2 }));
    await waitFor(() => {
      expect(onEvent).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(mockedFetchOrders.mock.calls.length).toBeGreaterThan(
        ordersCallsAfterOpen,
      );
    });
  });

  it("ignores a stale/out-of-order event (same or lower version)", async () => {
    const onEvent = vi.fn();
    renderProbe("token-abc", onEvent);
    await waitFor(() => expect(instances).toHaveLength(1));
    instances[0].open();
    instances[0].receive(guestEvent({ version: 5 }));
    instances[0].receive(guestEvent({ version: 5 }));
    instances[0].receive(guestEvent({ version: 3 }));
    instances[0].receive(guestEvent({ version: 6 }));
    await waitFor(() => {
      expect(onEvent).toHaveBeenCalledTimes(2);
    });
  });

  it("closes the socket on unmount (no leaked sockets)", async () => {
    const utils = renderProbe();
    await waitFor(() => expect(instances).toHaveLength(1));
    instances[0].open();
    utils.unmount();
    expect(instances[0].readyState).toBe(3);
  });

  it("reconnects with a fresh ticket after a network close", async () => {
    mockedFetchTicket
      .mockResolvedValueOnce({
        data: { ticket: "ticket-1", channel: "guest", expiresAt: "2099-01-01T00:00:00Z" },
      })
      .mockResolvedValueOnce({
        data: { ticket: "ticket-2", channel: "guest", expiresAt: "2099-01-01T00:00:00Z" },
      });
    renderProbe();
    await waitFor(() => expect(instances).toHaveLength(1));
    instances[0].open();
    await waitFor(() => {
      expect(screen.getByTestId("connection")).toHaveTextContent("live");
    });
    instances[0].closeWith(1006);
    await waitFor(() => {
      expect(screen.getByTestId("connection")).toHaveTextContent("reconnecting");
    });
    await waitFor(() => expect(instances).toHaveLength(2));
    expect(mockedFetchTicket).toHaveBeenCalledTimes(2);
    expect(instances[1].url).toContain("ticket=ticket-2");
  });

  it("recovers from an expired ticket (4401) with a fresh ticket", async () => {
    mockedFetchTicket
      .mockResolvedValueOnce({
        data: { ticket: "ticket-1", channel: "guest", expiresAt: "2099-01-01T00:00:00Z" },
      })
      .mockResolvedValueOnce({
        data: { ticket: "ticket-2", channel: "guest", expiresAt: "2099-01-01T00:00:00Z" },
      });
    renderProbe();
    await waitFor(() => expect(instances).toHaveLength(1));
    instances[0].open();
    instances[0].closeWith(4401);
    await waitFor(() => expect(instances).toHaveLength(2));
    expect(instances[1].url).toContain("ticket=ticket-2");
  });

  it("stops after repeated session failures (401 on ticket fetch)", async () => {
    mockedFetchTicket.mockRejectedValue(
      new ApiError(401, "invalid_table_token", "bad token"),
    );
    renderProbe();
    // Three failed attempts (initial + 2 backoff retries), then stop.
    await waitFor(() => {
      expect(screen.getByTestId("connection")).toHaveTextContent("session-expired");
    });
    const calls = mockedFetchTicket.mock.calls.length;
    expect(calls).toBe(3);
    // No further attempts after giving up.
    await new Promise((r) => setTimeout(r, 100));
    expect(mockedFetchTicket.mock.calls.length).toBe(calls);
    expect(instances).toHaveLength(0);
  });

  it("a payment event only flips the bill to paid when REST says so", async () => {
    renderProbe();
    await waitFor(() => expect(instances).toHaveLength(1));
    instances[0].open();
    await waitFor(() => {
      expect(screen.getByTestId("connection")).toHaveTextContent("live");
    });
    // The payment event triggers a refetch; the backend still says
    // the bill is issued (e.g. the payment was for another session
    // or has not confirmed yet) — the UI must NOT show paid.
    const billCallsBefore = mockedFetchBill.mock.calls.length;
    instances[0].receive(
      guestEvent({
        type: "payment.recorded",
        version: 2,
        data: { bill_id: "bill-1", bill_status: "payment_requested", amount_paise: 4900 },
      }),
    );
    await waitFor(() => {
      expect(mockedFetchBill.mock.calls.length).toBeGreaterThan(billCallsBefore);
    });
    expect(screen.getByTestId("bill-status")).toHaveTextContent("issued");
    // Only when the refetch returns a paid bill does it flip.
    mockedFetchBill.mockResolvedValue(billData("paid"));
    instances[0].receive(
      guestEvent({
        type: "bill.paid",
        version: 3,
        data: { bill_id: "bill-1", receipt_number: "R-1" },
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("bill-status")).toHaveTextContent("paid");
    });
  });
});
