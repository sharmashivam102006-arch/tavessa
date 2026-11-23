/** DEMO tickets for `?demo=1` on /kitchen — clearly labelled sample
 * tickets, NOT live orders. Never mixed with live data, never shown
 * by default. Used for screenshots and for previewing the board
 * without a running backend. Prices are display-only; the backend
 * recomputes every price at order time.
 */
import type { KitchenOrder } from "@/shared/api/types";

function placedMinutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

export const DEMO_TICKETS: KitchenOrder[] = [
  {
    id: "demo-order-new",
    orderNumber: "ZAIKA-9001",
    tableSessionId: "demo-session-5",
    status: "placed",
    items: [
      {
        id: "demo-item-1",
        menuItemId: "paneer-tikka",
        name: "Paneer Tikka Angara",
        variantName: "Angara hot",
        addOnNames: ["Extra mint chutney"],
        quantity: 2,
        unitPricePaise: 26900,
        totalPaise: 53800,
        specialInstructions: "less spicy, no onion",
        status: "queued",
      },
      {
        id: "demo-item-2",
        menuItemId: "butter-naan",
        name: "Butter Naan",
        quantity: 3,
        unitPricePaise: 4900,
        totalPaise: 14700,
        status: "queued",
      },
    ],
    subtotalPaise: 68500,
    taxPaise: 0,
    totalPaise: 68500,
    placedAt: placedMinutesAgo(4),
    tableNumber: 5,
    elapsedSeconds: 4 * 60,
  },
  {
    id: "demo-order-preparing",
    orderNumber: "ZAIKA-9002",
    tableSessionId: "demo-session-7",
    status: "preparing",
    items: [
      {
        id: "demo-item-3",
        menuItemId: "butter-chicken",
        name: "Old Delhi Butter Chicken",
        variantName: "Half",
        addOnNames: ["Extra gravy"],
        quantity: 1,
        unitPricePaise: 29900,
        totalPaise: 29900,
        status: "preparing",
      },
      {
        id: "demo-item-4",
        menuItemId: "masala-chai",
        name: "Masala Chai",
        quantity: 2,
        unitPricePaise: 5900,
        totalPaise: 11800,
        specialInstructions: "extra ginger",
        status: "preparing",
      },
    ],
    subtotalPaise: 41700,
    taxPaise: 0,
    totalPaise: 41700,
    placedAt: placedMinutesAgo(11),
    acceptedAt: placedMinutesAgo(9),
    tableNumber: 7,
    elapsedSeconds: 11 * 60,
  },
  {
    id: "demo-order-ready",
    orderNumber: "ZAIKA-9003",
    tableSessionId: "demo-session-3",
    status: "ready",
    items: [
      {
        id: "demo-item-5",
        menuItemId: "samosa-chaat",
        name: "Samosa Chaat",
        quantity: 1,
        unitPricePaise: 17900,
        totalPaise: 17900,
        status: "ready",
      },
    ],
    subtotalPaise: 17900,
    taxPaise: 0,
    totalPaise: 17900,
    placedAt: placedMinutesAgo(18),
    acceptedAt: placedMinutesAgo(16),
    readyAt: placedMinutesAgo(2),
    tableNumber: 3,
    elapsedSeconds: 18 * 60,
  },
];
