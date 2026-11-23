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
import type { Bill, Receipt, StaffBillSummary } from "@/shared/api/types";
import { BillingQueue } from "@/features/staff/components/BillingQueue";
import { BillDetail } from "@/features/staff/components/BillDetail";
import {
  fetchStaffBillReceipt,
  recordStaffPayment,
} from "@/features/staff/api/staffApi";

vi.mock("@/features/staff/api/staffApi", () => ({
  getStaffDevKey: () => "dev-staff-only",
  isStaffConfigured: () => true,
  fetchStaffRestaurants: vi.fn(),
  fetchStaffTables: vi.fn(),
  fetchStaffTableDetail: vi.fn(),
  fetchStaffOrders: vi.fn(),
  serveStaffOrder: vi.fn(),
  fetchStaffHelpCalls: vi.fn(),
  acknowledgeHelpCall: vi.fn(),
  resolveHelpCall: vi.fn(),
  fetchStaffBills: vi.fn(),
  fetchStaffBill: vi.fn(),
  recordStaffPayment: vi.fn(),
  fetchStaffBillPayments: vi.fn(),
  fetchStaffBillReceipt: vi.fn(),
}));

const mockedRecord = vi.mocked(recordStaffPayment);
const mockedReceipt = vi.mocked(fetchStaffBillReceipt);

function summary(overrides: Partial<StaffBillSummary> = {}): StaffBillSummary {
  return {
    id: "bill-1",
    billNumber: "B-2026-11-23-001",
    tableId: "table-3",
    tableNumber: 3,
    tableSessionId: "session-3",
    status: "payment_requested",
    totalPaise: 54800,
    amountPaidPaise: 0,
    outstandingPaise: 54800,
    orderCount: 1,
    issuedAt: new Date(Date.now() - 40 * 60_000).toISOString(),
    paymentRequestedAt: new Date(Date.now() - 6 * 60_000).toISOString(),
    ...overrides,
  };
}

function bill(overrides: Partial<Bill> = {}): Bill {
  return {
    id: "bill-1",
    billNumber: "B-2026-11-23-001",
    tableSessionId: "session-3",
    tableId: "table-3",
    tableNumber: 3,
    status: "payment_requested",
    items: [
      {
        id: "item-1",
        orderId: "order-1",
        name: "Butter Naan",
        quantity: 2,
        unitPricePaise: 4900,
        totalPaise: 9800,
      },
    ],
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
    paymentRequestedAt: new Date().toISOString(),
    version: 1,
    ...overrides,
  };
}

function receipt(): Receipt {
  return {
    restaurantName: "Zaika",
    billNumber: "B-2026-11-23-001",
    receiptNumber: "R-2026-11-23-001",
    receiptNumbers: ["R-2026-11-23-001"],
    tableNumber: 3,
    orderNumbers: ["ZAIKA-0001"],
    items: [
      {
        id: "item-1",
        name: "Butter Naan",
        quantity: 2,
        unitPricePaise: 4900,
        totalPaise: 9800,
      },
    ],
    subtotalPaise: 9800,
    taxPaise: 0,
    taxStatus: "Tax: not configured — no tax rate has been set for this restaurant",
    totalPaise: 9800,
    amountReceivedPaise: 9800,
    outstandingPaise: 0,
    paymentMethod: "Cash",
    paymentTimestamps: [new Date().toISOString()],
    receiptTimestamp: new Date().toISOString(),
    billId: "bill-1",
  };
}

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("BillingQueue (Stage 6)", () => {
  it("lists pending bills with table, total and outstanding", () => {
    renderWithClient(
      <BillingQueue
        bills={[
          summary(),
          summary({
            id: "bill-2",
            billNumber: "B-2026-11-23-002",
            tableNumber: 2,
            status: "issued",
            totalPaise: 29900,
            outstandingPaise: 29900,
            paymentRequestedAt: undefined,
          }),
        ]}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText(/Table 3 · B-2026-11-23-001/)).toBeInTheDocument();
    expect(screen.getByText("Payment requested")).toBeInTheDocument();
    expect(screen.getByText(/Table 2 · B-2026-11-23-002/)).toBeInTheDocument();
    expect(screen.getByText("Issued")).toBeInTheDocument();
    expect(screen.getByText("₹548.00")).toBeInTheDocument();
  });

  it("shows the empty state when nothing awaits cash", () => {
    renderWithClient(<BillingQueue bills={[]} onSelect={() => {}} />);
    expect(screen.getByText("No bills awaiting cash.")).toBeInTheDocument();
  });

  it("drops paid bills — they must never sit in the queue as 'Issued'", () => {
    renderWithClient(
      <BillingQueue
        bills={[
          summary({
            id: "bill-paid",
            billNumber: "B-2026-11-23-001",
            tableNumber: 5,
            status: "paid",
            amountPaidPaise: 54800,
            outstandingPaise: 0,
          }),
          summary(),
        ]}
        onSelect={() => {}}
      />,
    );
    expect(screen.queryByText(/Table 5 · B-2026-11-23-001/)).not.toBeInTheDocument();
    expect(screen.getByText(/Table 3 · B-2026-11-23-001/)).toBeInTheDocument();
    expect(screen.queryByText("Issued")).not.toBeInTheDocument();
  });
});

describe("BillDetail cash collection (Stage 6)", () => {
  it("prefills the outstanding amount and confirms in two steps", async () => {
    const live = bill();
    mockedReceipt.mockResolvedValue({ data: receipt() });
    mockedRecord.mockResolvedValue({
      data: {
        payment: {
          id: "pay-1",
          billId: "bill-1",
          method: "cash",
          amountPaise: 9800,
          changeDuePaise: 0,
          collectedBy: null,
          collectedAt: new Date().toISOString(),
          receiptNumber: "R-2026-11-23-001",
          status: "confirmed",
        },
        bill: { ...live, status: "paid", amountPaidPaise: 9800, outstandingPaise: 0 },
        receipt: receipt(),
      },
    });
    const user = userEvent.setup();
    renderWithClient(<BillDetail bill={live} restaurantId="rest-1" />);

    const amountInput = screen.getByLabelText(/Amount \(₹\)/) as HTMLInputElement;
    await waitFor(() => expect(amountInput.value).toBe("98.00"));
    await user.click(screen.getByRole("button", { name: /Record cash/ }));
    // Confirm panel shows the exact amount before anything is sent.
    expect(
      screen.getByText(/Confirm cash collection/),
    ).toBeInTheDocument();
    expect(mockedRecord).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(mockedRecord).toHaveBeenCalledTimes(1));
    const args = mockedRecord.mock.calls[0];
    expect(args[0]).toBe("rest-1");
    expect(args[1]).toBe("bill-1");
    expect(args[2]).toEqual({ amountPaise: 9800 });
    // Receipt shown after confirmation.
    expect(await screen.findByText("R-2026-11-23-001")).toBeInTheDocument();
  });

  it("rejects an amount above the outstanding and never calls the API", async () => {
    const user = userEvent.setup();
    renderWithClient(<BillDetail bill={bill()} restaurantId="rest-1" />);
    const amountInput = screen.getByLabelText(/Amount \(₹\)/);
    await user.clear(amountInput);
    await user.type(amountInput, "200.00");
    expect(
      screen.getByText(/more than the ₹98.00 due/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Record cash/ }),
    ).toBeDisabled();
    expect(mockedRecord).not.toHaveBeenCalled();
  });

  it("shows the error and leaves the bill in place when confirmation fails", async () => {
    mockedRecord.mockRejectedValueOnce(
      new ApiError(422, "amount_exceeds_due", "The payment is more than the amount due."),
    );
    const user = userEvent.setup();
    renderWithClient(<BillDetail bill={bill()} restaurantId="rest-1" />);
    await user.click(screen.getByRole("button", { name: /Record cash/ }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(
        screen.getByText(/more than the amount due/),
      ).toBeInTheDocument(),
    );
    // The bill is still unpaid and the collect form is still there.
    expect(screen.getByText(/Collect cash/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Amount \(₹\)/)).toBeInTheDocument();
  });

  it("disables actions in demo mode", async () => {
    renderWithClient(<BillDetail bill={bill()} restaurantId="demo" demo />);
    expect(
      screen.getByRole("button", { name: /Record cash \(disabled in demo\)/ }),
    ).toBeDisabled();
  });

  it("shows the receipt for an already-paid bill", async () => {
    mockedReceipt.mockResolvedValue({ data: receipt() });
    const paid = bill({
      status: "paid",
      amountPaidPaise: 9800,
      outstandingPaise: 0,
      payments: [
        {
          id: "pay-1",
          billId: "bill-1",
          method: "cash",
          amountPaise: 9800,
          changeDuePaise: 0,
          collectedBy: null,
          collectedAt: new Date().toISOString(),
          receiptNumber: "R-2026-11-23-001",
          status: "confirmed",
        },
      ],
    });
    renderWithClient(<BillDetail bill={paid} restaurantId="rest-1" />);
    expect(await screen.findByText("R-2026-11-23-001")).toBeInTheDocument();
    expect(screen.queryByText(/Collect cash/)).not.toBeInTheDocument();
  });
});
