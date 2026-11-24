/** Auth + admin API — the only place auth/admin endpoints are called.
 * Every function goes through the shared client (no second client).
 * The access token is attached by the client from the in-memory
 * auth store; the refresh cookie is sent by the browser.
 */
import { api, createIdempotencyKey } from "@/shared/api/client";
import type { AuthUser } from "@/shared/stores/authStore";
import type {
  AdminBill,
  AdminCategory,
  AdminMenuItem,
  AdminOrder,
  AdminPayment,
  AdminRefund,
  AdminTable,
  AdminUser,
  AuditLogRow,
  BranchSettings,
  DailyCash,
  DashboardOverview,
  Page,
  RestaurantSettings,
  TaxConfig,
} from "@/shared/api/types";

const V1 = "/api/v1";

export interface LoginResult {
  accessToken: string;
  tokenType: string;
  user: AuthUser;
}

export async function login(email: string, password: string): Promise<LoginResult> {
  const body = await api.post<{ data: LoginResult }>(
    `${V1}/auth/login`,
    { email, password },
    { noRefresh: true },
  );
  return body.data;
}

export async function logout(): Promise<void> {
  try {
    await api.post(`${V1}/auth/logout`, {}, { noRefresh: true });
  } catch {
    // Logout is best-effort client-side; the cookie is cleared server-side.
  }
}

export async function fetchMe(): Promise<AuthUser> {
  const body = await api.get<{ data: AuthUser }>(`${V1}/auth/me`);
  return body.data;
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<AuthUser> {
  const body = await api.post<{ data: AuthUser }>(`${V1}/auth/change-password`, {
    currentPassword,
    newPassword,
  });
  return body.data;
}

export async function fetchWsTicket(
  channel: "kitchen" | "staff",
): Promise<{ ticket: string; channel: string; expiresAt: string }> {
  const body = await api.post<{
    data: { ticket: string; channel: string; expiresAt: string };
  }>(`${V1}/ws/tickets`, { channel });
  return body.data;
}

/* ------------------------------------------------------------------ */
/* Admin                                                               */
/* ------------------------------------------------------------------ */

function qs(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function fetchDashboard(
  preset = "today",
  from?: string,
  to?: string,
): Promise<DashboardOverview> {
  const body = await api.get<{ data: DashboardOverview }>(
    `${V1}/admin/dashboard/overview${qs({ preset, from, to })}`,
  );
  return body.data;
}

export async function fetchAdminOrders(params: {
  status?: string;
  page?: number;
}): Promise<Page<AdminOrder>> {
  return api.get<Page<AdminOrder>>(
    `${V1}/admin/orders${qs({ status: params.status, page: params.page })}`,
  );
}

export async function fetchAdminBills(params: {
  status?: string;
  page?: number;
}): Promise<Page<AdminBill>> {
  return api.get<Page<AdminBill>>(
    `${V1}/admin/bills${qs({ status: params.status, page: params.page })}`,
  );
}

export async function fetchAdminPayments(page = 1): Promise<Page<AdminPayment>> {
  return api.get<Page<AdminPayment>>(`${V1}/admin/payments${qs({ page })}`);
}

export async function fetchAdminRefunds(page = 1): Promise<Page<AdminRefund>> {
  return api.get<Page<AdminRefund>>(`${V1}/admin/refunds${qs({ page })}`);
}

export async function fetchDailyCash(date: string): Promise<DailyCash> {
  const body = await api.get<{ data: DailyCash }>(
    `${V1}/admin/reports/daily-cash${qs({ date })}`,
  );
  return body.data;
}

export async function fetchDailyCashCsv(date: string): Promise<string> {
  const body = await api.get<{ data: { csv: string } }>(
    `${V1}/admin/reports/daily-cash${qs({ date, format: "csv" })}`,
  );
  return body.data.csv;
}

export async function voidBill(billId: string, reason: string): Promise<void> {
  await api.post(`${V1}/admin/bills/${billId}/void`, { reason });
}

export async function createRefund(
  billId: string,
  paymentId: string,
  amountPaise: number,
  reason: string,
): Promise<AdminRefund> {
  const body = await api.post<{ data: AdminRefund }>(
    `${V1}/admin/bills/${billId}/refunds`,
    { paymentId, amountPaise, reason },
    { headers: { "Idempotency-Key": createIdempotencyKey() } },
  );
  return body.data;
}

/* Menu */

export async function fetchCategories(): Promise<AdminCategory[]> {
  const body = await api.get<{ data: AdminCategory[] }>(
    `${V1}/admin/menu/categories`,
  );
  return body.data;
}

export async function createCategory(name: string): Promise<AdminCategory> {
  const body = await api.post<{ data: AdminCategory }>(
    `${V1}/admin/menu/categories`,
    { name },
  );
  return body.data;
}

export async function reorderCategories(
  categoryIds: string[],
): Promise<AdminCategory[]> {
  const body = await api.post<{ data: AdminCategory[] }>(
    `${V1}/admin/menu/categories/reorder`,
    { categoryIds },
  );
  return body.data;
}

export async function fetchMenuItems(page = 1): Promise<Page<AdminMenuItem>> {
  return api.get<Page<AdminMenuItem>>(
    `${V1}/admin/menu/items${qs({ page, per_page: 100 })}`,
  );
}

export async function createMenuItem(input: {
  categoryId: string;
  name: string;
  pricePaise: number;
  isVeg?: boolean;
  station?: string;
}): Promise<AdminMenuItem> {
  const body = await api.post<{ data: AdminMenuItem }>(
    `${V1}/admin/menu/items`,
    input,
  );
  return body.data;
}

export async function updateMenuItem(
  itemId: string,
  patch: Partial<AdminMenuItem> & { pricePaise?: number; isAvailable?: boolean },
): Promise<AdminMenuItem> {
  const body = await api.patch<{ data: AdminMenuItem }>(
    `${V1}/admin/menu/items/${itemId}`,
    patch,
  );
  return body.data;
}

export async function createVariant(
  itemId: string,
  name: string,
  priceDeltaPaise: number,
): Promise<void> {
  await api.post(`${V1}/admin/menu/items/${itemId}/variants`, {
    name,
    priceDeltaPaise,
  });
}

export async function createAddOn(
  itemId: string,
  name: string,
  pricePaise: number,
): Promise<void> {
  await api.post(`${V1}/admin/menu/items/${itemId}/add-ons`, {
    name,
    pricePaise,
  });
}

/* Users */

export async function fetchUsers(page = 1): Promise<Page<AdminUser>> {
  return api.get<Page<AdminUser>>(`${V1}/admin/users${qs({ page })}`);
}

export async function createUser(input: {
  email: string;
  password: string;
  role: string;
  canManageStaff?: boolean;
  canManageSettings?: boolean;
  canRefund?: boolean;
  canVoid?: boolean;
}): Promise<AdminUser> {
  const body = await api.post<{ data: AdminUser }>(`${V1}/admin/users`, input);
  return body.data;
}

export async function updateUser(
  userId: string,
  patch: Record<string, unknown>,
): Promise<AdminUser> {
  const body = await api.patch<{ data: AdminUser }>(
    `${V1}/admin/users/${userId}`,
    patch,
  );
  return body.data;
}

export async function deactivateUser(userId: string): Promise<AdminUser> {
  const body = await api.post<{ data: AdminUser }>(
    `${V1}/admin/users/${userId}/deactivate`,
    {},
  );
  return body.data;
}

export async function resetUserPassword(
  userId: string,
  password: string,
): Promise<AdminUser> {
  const body = await api.post<{ data: AdminUser }>(
    `${V1}/admin/users/${userId}/reset-password`,
    { password },
  );
  return body.data;
}

/* Settings + tax + audit */

export async function fetchRestaurantSettings(): Promise<RestaurantSettings> {
  const body = await api.get<{ data: RestaurantSettings }>(
    `${V1}/admin/settings/restaurant`,
  );
  return body.data;
}

export async function updateRestaurantSettings(
  patch: Record<string, unknown>,
): Promise<RestaurantSettings> {
  const body = await api.patch<{ data: RestaurantSettings }>(
    `${V1}/admin/settings/restaurant`,
    patch,
  );
  return body.data;
}

export async function fetchBranches(): Promise<BranchSettings[]> {
  const body = await api.get<{ data: BranchSettings[] }>(
    `${V1}/admin/settings/branches`,
  );
  return body.data;
}

export async function updateBranch(
  branchId: string,
  patch: Record<string, unknown>,
): Promise<BranchSettings> {
  const body = await api.patch<{ data: BranchSettings }>(
    `${V1}/admin/settings/branches/${branchId}`,
    patch,
  );
  return body.data;
}

export async function fetchTables(): Promise<AdminTable[]> {
  const body = await api.get<{ data: AdminTable[] }>(
    `${V1}/admin/settings/tables`,
  );
  return body.data;
}

export async function createTable(number: number): Promise<AdminTable> {
  const body = await api.post<{ data: AdminTable }>(
    `${V1}/admin/settings/tables`,
    { number },
  );
  return body.data;
}

export async function fetchTax(): Promise<{
  current: TaxConfig | null;
  history: TaxConfig[];
}> {
  return api.get<{ current: TaxConfig | null; history: TaxConfig[] }>(
    `${V1}/admin/settings/tax`,
  );
}

export async function createTaxConfig(input: {
  enabled: boolean;
  rateBps: number | null;
  mode: string;
  label: string | null;
  effectiveFrom: string;
}): Promise<{ current: TaxConfig | null; history: TaxConfig[] }> {
  return api.post(`${V1}/admin/settings/tax`, input);
}

export async function fetchAuditLogs(params: {
  action?: string;
  page?: number;
}): Promise<Page<AuditLogRow>> {
  return api.get<Page<AuditLogRow>>(
    `${V1}/admin/audit-logs${qs({ action: params.action, page: params.page })}`,
  );
}
