// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

// Vitest here runs without globals, so Testing Library's
// auto-cleanup does not register itself — clean up explicitly.
afterEach(() => cleanup());

import { ApiError } from "@/shared/api/client";
import type { Bill, Receipt } from "@/shared/api/types";
import { BillPage } from "@/features/guest/BillPage";
import {
  createBillSplit,
  fetchGuestBill,
  fetchGuestReceipt,
  requestBill,
  requestCashPayment,
} from "@/features/guest/api/guestApi";

vi.mock("@/features/guest/api/guestApi", () => ({
  fetchGuestSession: vi.fn(),
  fetchGuestSessionCurrent: vi.fn(),
  fetchGuestMenu: vi.fn(),
  placeOrder: vi.fn(),
  fetchGuestOrders: vi.fn(),
  fetchGuestOrder: vi.fn(),
  cancelOrder: vi.fn(),
  requestHelpCall: vi.fn(),
  fetchGuestBill: vi.fn(),
  requestBill: vi.fn(),
  requestCashPayment: vi.fn(),
  createBillSplit: vi.fn(),
  fetchGuestReceipt: vi.fn(),
  submitReview: vi.fn(),
}));

const mockedFetchBill = vi.mocked(fetchGuestBill);
const mockedFetchReceipt = vi.mocked(fetchGuestReceipt);
const mockedRequestBill = vi.mocked(requestBill);
const mockedRequestCash = vi.mocked(requestCashPayment);
const mockedCreateSplit = vi.mocked(createBillSplit);

function bill(overrides: Partial<Bill> = {}): Bill {
  return {
    id: "bill-1",
    billNumber: "B-2026-11-23-001",
    tableSessionId: "session-1",
    tableId: "table-1",
    tableNumber: 5,
    status: "issued",
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
    tableNumber: 5,
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

function renderBillPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/t/token-1/bill"]}>
        <Routes>
          <Route path="/t/:tableToken/bill" element={<BillPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("BillPage (Stage 6, live API)", () => {
  it("shows the no-bill state and requests the bill", async () => {
    mockedFetchBill.mockRejectedValueOnce(
      new ApiError(404, "bill_not_found", "No bill exists for this table yet."),
    );
    const live = bill();
    mockedFetchBill.mockResolvedValueOnce({ data: live });
    mockedRequestBill.mockResolvedValue({ data: live });
    const user = userEvent.setup();
    renderBillPage();

    expect(await screen.findByText("No bill yet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Request bill" }));
    expect(mockedRequestBill).toHaveBeenCalledWith("token-1");
    expect(await screen.findByText(/Bill B-2026-11-23-001/)).toBeInTheDocument();
    expect(screen.getAllByText(/Butter Naan/).length).toBeGreaterThan(0);
  });

  it("shows the itemised bill, outstanding balance and tax as not configured", async () => {
    mockedFetchBill.mockResolvedValue({ data: bill() });
    renderBillPage();

    expect(await screen.findByText(/Bill B-2026-11-23-001/)).toBeInTheDocument();
    expect(screen.getByText("ZAIKA-0001", { exact: false })).toBeInTheDocument();
    // Outstanding = total (nothing paid yet): ₹98.00 appears for
    // both the total and the outstanding rows.
    expect(screen.getByText("Outstanding")).toBeInTheDocument();
    expect(screen.getAllByText("₹98.00").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Not configured yet (D1)")).toBeInTheDocument();
    // No online payment buttons anywhere — only Pay by Cash.
    expect(
      screen.queryByRole("button", { name: /pay online|UPI/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Pay by Cash/ }),
    ).toBeInTheDocument();
  });

  it("Pay by Cash requests payment and does not show paid or a receipt", async () => {
    const live = bill();
    mockedFetchBill.mockResolvedValue({ data: live });
    mockedRequestCash.mockResolvedValue({
      data: { ...live, status: "payment_requested" },
    });
    const user = userEvent.setup();
    renderBillPage();

    expect(await screen.findByText(/Bill B-2026-11-23-001/)).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: /Pay by Cash/ }),
    );
    expect(mockedRequestCash).toHaveBeenCalledWith("token-1");
    // The page re-fetches; the mocked bill is still "issued", so the
    // waiting copy comes from the action note.
    await waitFor(() =>
      expect(screen.getByText(/A waiter is on the way/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Receipt/)).not.toBeInTheDocument();
    expect(mockedFetchReceipt).not.toHaveBeenCalled();
  });

  it("shows the receipt only after the bill is paid", async () => {
    const paid = bill({
      status: "paid",
      amountPaidPaise: 9800,
      outstandingPaise: 0,
      paidAt: new Date().toISOString(),
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
    mockedFetchBill.mockResolvedValue({ data: paid });
    mockedFetchReceipt.mockResolvedValue({ data: receipt() });
    renderBillPage();

    expect(await screen.findByText(/Bill B-2026-11-23-001/)).toBeInTheDocument();
    expect(await screen.findByText("R-2026-11-23-001")).toBeInTheDocument();
    expect(screen.getByText("Paid — see your receipt below.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pay by Cash/ })).not.toBeInTheDocument();
  });

  it("shows a loading state, then an error state with retry", async () => {
    mockedFetchBill.mockRejectedValueOnce(
      new ApiError(500, "internal_error", "Something went wrong."),
    );
    renderBillPage();
    expect(screen.getByText(/Loading your bill/)).toBeInTheDocument();
    expect(
      await screen.findByText(/Could not load your bill/),
    ).toBeInTheDocument();
  });

  it("saves an equal split through the server-validated endpoint", async () => {
    const live = bill();
    mockedFetchBill.mockResolvedValue({ data: live });
    mockedCreateSplit.mockResolvedValue({
      data: {
        ...live,
        splits: [
          { id: "s1", label: "Split 1", amountPaise: 4900, status: "unpaid" },
          { id: "s2", label: "Split 2", amountPaise: 4900, status: "unpaid" },
        ],
      },
    });
    const user = userEvent.setup();
    renderBillPage();

    expect(await screen.findByText(/Bill B-2026-11-23-001/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save this split" }));
    expect(mockedCreateSplit).toHaveBeenCalledWith("token-1", {
      mode: "equal",
      parts: 2,
    });
  });
});
