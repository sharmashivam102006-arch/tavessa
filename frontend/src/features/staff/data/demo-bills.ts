/** DEMO bills for `?demo=1` on /staff — clearly labelled sample
 * data, NOT live state. Never mixed with live data, never shown by
 * default. Actions on demo bills are disabled — nothing here is
 * sent to the server.
 */
import type { Bill, StaffBillSummary } from "@/shared/api/types";

function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

export const DEMO_BILLS: StaffBillSummary[] = [
  {
    id: "demo-bill-1",
    billNumber: "B-2026-11-23-001",
    tableId: "demo-table-3",
    tableNumber: 3,
    tableSessionId: "demo-session-3",
    status: "payment_requested",
    totalPaise: 54800,
    amountPaidPaise: 0,
    outstandingPaise: 54800,
    orderCount: 1,
    issuedAt: minutesAgo(40),
    paymentRequestedAt: minutesAgo(6),
  },
  {
    id: "demo-bill-2",
    billNumber: "B-2026-11-23-002",
    tableId: "demo-table-2",
    tableNumber: 2,
    tableSessionId: "demo-session-2",
    status: "issued",
    totalPaise: 29900,
    amountPaidPaise: 0,
    outstandingPaise: 29900,
    orderCount: 1,
    issuedAt: minutesAgo(18),
  },
];

export const DEMO_BILL_DETAIL: Bill = {
  id: "demo-bill-1",
  billNumber: "B-2026-11-23-001",
  tableSessionId: "demo-session-3",
  tableId: "demo-table-3",
  tableNumber: 3,
  status: "payment_requested",
  items: [
    {
      id: "demo-bill-item-1",
      orderId: "demo-order-1",
      name: "Butter Chicken",
      quantity: 1,
      unitPricePaise: 29900,
      totalPaise: 29900,
    },
    {
      id: "demo-bill-item-2",
      orderId: "demo-order-1",
      name: "Butter Naan",
      quantity: 1,
      unitPricePaise: 4900,
      totalPaise: 4900,
    },
    {
      id: "demo-bill-item-3",
      orderId: "demo-order-1",
      name: "Paneer Tikka",
      quantity: 1,
      variantName: "Full",
      unitPricePaise: 20000,
      totalPaise: 20000,
    },
  ],
  splits: [],
  payments: [],
  orderNumbers: ["ZAIKA-0001"],
  subtotalPaise: 54800,
  taxPaise: 0,
  serviceChargePaise: 0,
  discountPaise: 0,
  totalPaise: 54800,
  amountPaidPaise: 0,
  outstandingPaise: 54800,
  issuedAt: minutesAgo(40),
  paymentRequestedAt: minutesAgo(6),
  version: 1,
};
