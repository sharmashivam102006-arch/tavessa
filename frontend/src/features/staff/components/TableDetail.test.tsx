// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => cleanup());

import type { StaffTableDetail } from "@/shared/api/types";
import { TableDetail } from "@/features/staff/components/TableDetail";

vi.mock("@/features/staff/api/staffApi", () => ({
  acknowledgeHelpCall: vi.fn(),
  resolveHelpCall: vi.fn(),
  serveStaffOrder: vi.fn(),
}));

function detail(overrides: Partial<StaffTableDetail> = {}): StaffTableDetail {
  return {
    table: {
      id: "table-1",
      number: 5,
      status: "occupied",
      sessionId: "session-1",
      sessionOpenedAt: new Date(Date.now() - 30 * 60_000).toISOString(),
      activeOrderCount: 1,
      readyOrderCount: 0,
      latestOrderStatus: "placed",
      hasOpenHelpCall: false,
    },
    orders: [],
    helpCalls: [],
    openBill: null,
    ...overrides,
  };
}

describe("TableDetail session line (Stage 9)", () => {
  it("shows 'Session open since' for an occupied table (backend shape)", () => {
    // Regression: the panel used to read detail.session, a field
    // the backend never sends, so every occupied table read
    // "No active session". The session now comes from the table row.
    render(
      <TableDetail detail={detail()} restaurantId="rest-1" onClose={() => {}} />,
    );
    expect(screen.getByText(/Session open since/)).toBeInTheDocument();
    expect(screen.queryByText(/No active session/)).not.toBeInTheDocument();
  });

  it("shows 'No active session' only when the table really is free", () => {
    render(
      <TableDetail
        detail={detail({
          table: {
            id: "table-1",
            number: 5,
            status: "available",
            sessionId: null,
            sessionOpenedAt: null,
            activeOrderCount: 0,
            readyOrderCount: 0,
            latestOrderStatus: null,
            hasOpenHelpCall: false,
          },
        })}
        restaurantId="rest-1"
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/No active session/)).toBeInTheDocument();
  });
});
