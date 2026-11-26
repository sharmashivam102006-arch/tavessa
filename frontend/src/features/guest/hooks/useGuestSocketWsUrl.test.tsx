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

// The hook reads VITE_WS_URL once, at module load, so the variable
// must be stubbed BEFORE the hook module is imported. This file
// therefore imports the hook dynamically (top-level await) instead
// of statically. No vi.resetModules is used: a reset would create a
// second copy of @tanstack/react-query and break the provider.
vi.stubEnv("VITE_WS_URL", "wss://tavessa-api.onrender.com");

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
  const actual =
    await importOriginal<typeof import("@/features/guest/lib/guestSocket")>();
  return {
    ...actual,
    // Reconnects happen ~instantly in tests (real backoff is
    // covered by guestSocket.test.ts).
    nextGuestBackoffDelayMs: () => 10,
  };
});

// Dynamic imports: evaluated after the stubEnv above, so the hook
// module sees VITE_WS_URL set. The ticket mock below is the same
// mock instance the hook uses.
const { useGuestSocket } = await import(
  "@/features/guest/hooks/useGuestSocket"
);
const { fetchGuestWsTicket } = await import(
  "@/features/guest/api/guestApi"
);
const mockedFetchTicket = vi.mocked(fetchGuestWsTicket);

type FakeSocket = {
  url: string;
  readyState: number;
  onopen: ((e: unknown) => void) | null;
  onmessage: ((e: { data: string }) => void) | null;
  onclose: ((e: { code: number }) => void) | null;
  onerror: ((e: unknown) => void) | null;
  close: (code?: number) => void;
  open: () => void;
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
  }
  vi.stubGlobal("WebSocket", Fake);
}

function Probe({ tableToken }: { tableToken: string }) {
  const { connection } = useGuestSocket(tableToken);
  return <p data-testid="connection">{connection}</p>;
}

function renderProbe(tableToken = "token-abc") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Probe tableToken={tableToken} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  installFakeWebSocket();
  mockedFetchTicket.mockResolvedValue({
    data: {
      ticket: "ticket-1",
      channel: "guest",
      expiresAt: "2099-01-01T00:00:00Z",
    },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe("useGuestSocket with VITE_WS_URL set", () => {
  it("connects directly to Render, not to the page origin", async () => {
    renderProbe();
    await waitFor(() => expect(instances).toHaveLength(1));
    // Exact URL: host is Render, path and ticket preserved, and
    // nothing from the same-origin fallback (the jsdom page origin)
    // leaks in.
    expect(instances[0].url).toBe(
      "wss://tavessa-api.onrender.com/ws/guest?ticket=ticket-1",
    );
    instances[0].open();
    await waitFor(() => {
      expect(screen.getByTestId("connection")).toHaveTextContent("live");
    });
  });
});
