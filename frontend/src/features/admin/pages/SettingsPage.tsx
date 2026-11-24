/** Settings — Stage 7 § F + § G. Restaurant + branch settings
 * and the tax configuration (D1) with its history. The
 * unconfigured state is shown clearly: "Tax: not configured" is
 * not a 0% rate, and no tax compliance is claimed anywhere. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api/client";
import {
  createTaxConfig,
  fetchBranches,
  fetchRestaurantSettings,
  fetchTax,
  updateBranch,
  updateRestaurantSettings,
} from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/features/admin/components/AdminBits";

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [branchTz, setBranchTz] = useState("");
  const [taxEnabled, setTaxEnabled] = useState(true);
  const [taxRate, setTaxRate] = useState("5");
  const [taxMode, setTaxMode] = useState("exclusive");
  const [taxLabel, setTaxLabel] = useState("GST");

  const restaurantQuery = useQuery({
    queryKey: ["admin-restaurant-settings"],
    queryFn: fetchRestaurantSettings,
    retry: false,
  });
  const branchesQuery = useQuery({
    queryKey: ["admin-branches"],
    queryFn: fetchBranches,
    retry: false,
  });
  const taxQuery = useQuery({
    queryKey: ["admin-tax"],
    queryFn: fetchTax,
    retry: false,
  });
  const fail = (error: unknown) =>
    setMessage(error instanceof ApiError ? error.message : "Something went wrong.");
  const invalidate = (key: string) => void queryClient.invalidateQueries({ queryKey: [key] });

  const saveRestaurant = useMutation({
    mutationFn: () =>
      updateRestaurantSettings({
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(phone.trim() ? { phone: phone.trim() } : {}),
      }),
    onSuccess: () => {
      setMessage("Restaurant settings saved.");
      invalidate("admin-restaurant-settings");
    },
    onError: fail,
  });
  const saveBranch = useMutation({
    mutationFn: (branchId: string) => updateBranch(branchId, { timezone: branchTz }),
    onSuccess: () => {
      setMessage("Branch settings saved.");
      invalidate("admin-branches");
    },
    onError: fail,
  });
  const saveTax = useMutation({
    mutationFn: () =>
      createTaxConfig({
        enabled: taxEnabled,
        rateBps: taxEnabled ? Math.round(Number(taxRate) * 100) : null,
        mode: taxMode,
        label: taxLabel.trim() || null,
        effectiveFrom: new Date().toISOString(),
      }),
    onSuccess: () => {
      setMessage("Tax configuration saved. Bills issued from now use it; frozen bills are unchanged.");
      invalidate("admin-tax");
    },
    onError: fail,
  });

  const restaurant = restaurantQuery.data;
  const branches = branchesQuery.data ?? [];
  const tax = taxQuery.data;

  return (
    <div>
      <PageHeader title="Settings" sub="Restaurant, branch and tax configuration." />
      {message && (
        <p role="status" className="mb-3 rounded border border-stone-300 bg-white p-3 text-sm">
          {message}
        </p>
      )}
      {(restaurantQuery.isPending || branchesQuery.isPending || taxQuery.isPending) && <LoadingState />}
      {(restaurantQuery.isError || branchesQuery.isError || taxQuery.isError) && (
        <ErrorState message="Could not load settings." />
      )}
      <div className="space-y-6">
        {restaurant && (
          <section className="rounded-lg border border-stone-200 bg-white p-4">
            <h2 className="font-semibold">Restaurant — {restaurant.name}</h2>
            <p className="text-sm text-stone-600">
              Currency: {restaurant.currency} · {restaurant.address ?? "No address set"}
            </p>
            <form
              className="mt-3 flex flex-wrap gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                saveRestaurant.mutate();
              }}
            >
              <input
                aria-label="Restaurant display name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={restaurant.name}
                className="rounded border border-stone-300 px-2 py-1.5 text-sm"
              />
              <input
                aria-label="Restaurant phone"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder={restaurant.phone ?? "Phone"}
                className="rounded border border-stone-300 px-2 py-1.5 text-sm"
              />
              <button className="rounded bg-stone-900 px-3 py-1.5 text-sm text-white">Save restaurant</button>
            </form>
          </section>
        )}
        {branches.length > 0 && (
          <section className="rounded-lg border border-stone-200 bg-white p-4">
            <h2 className="font-semibold">Branches</h2>
            <ul className="mt-2 space-y-3">
              {branches.map((b) => (
                <li key={b.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">{b.name}</span>
                  <span className="text-stone-500">Timezone: {b.timezone}</span>
                  <input
                    aria-label={`Timezone for ${b.name}`}
                    value={branchTz}
                    onChange={(e) => setBranchTz(e.target.value)}
                    placeholder="Asia/Kolkata"
                    className="rounded border border-stone-300 px-2 py-1 text-sm"
                  />
                  <button
                    onClick={() => saveBranch.mutate(b.id)}
                    className="rounded border px-2 py-1 text-xs"
                  >
                    Save timezone
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section className="rounded-lg border border-stone-200 bg-white p-4">
          <h2 className="font-semibold">Tax configuration</h2>
          {tax && tax.current === null && (
            <p className="mt-2 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              Tax: not configured — no tax rate has been set for this restaurant. Bills currently carry no
              tax. This is not a 0% rate; setting 0% explicitly is a separate, deliberate choice.
            </p>
          )}
          {tax && tax.current && (
            <p className="mt-2 text-sm">
              Current:{" "}
              {tax.current.enabled
                ? `${tax.current.label ?? "Tax"} ${(tax.current.rateBps ?? 0) / 100}% (${tax.current.mode})`
                : "Tax disabled"}{" "}
              · effective from {new Date(tax.current.effectiveFrom).toLocaleString()}
            </p>
          )}
          <form
            className="mt-3 flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              saveTax.mutate();
            }}
          >
            <label className="flex items-center gap-1 text-sm">
              <input
                type="checkbox"
                checked={taxEnabled}
                onChange={(e) => setTaxEnabled(e.target.checked)}
              />
              Tax enabled
            </label>
            <label className="text-sm">
              Rate %
              <input
                aria-label="Tax rate percent"
                value={taxRate}
                onChange={(e) => setTaxRate(e.target.value)}
                inputMode="decimal"
                disabled={!taxEnabled}
                className="ml-1 w-20 rounded border border-stone-300 px-2 py-1.5 disabled:opacity-50"
              />
            </label>
            <label className="text-sm">
              Mode
              <select
                aria-label="Tax mode"
                value={taxMode}
                onChange={(e) => setTaxMode(e.target.value)}
                disabled={!taxEnabled}
                className="ml-1 rounded border border-stone-300 px-2 py-1.5 disabled:opacity-50"
              >
                <option value="exclusive">Exclusive (added to subtotal)</option>
                <option value="inclusive">Inclusive (inside subtotal)</option>
              </select>
            </label>
            <input
              aria-label="Tax label"
              value={taxLabel}
              onChange={(e) => setTaxLabel(e.target.value)}
              placeholder="Label, e.g. GST"
              className="rounded border border-stone-300 px-2 py-1.5 text-sm"
            />
            <button className="rounded bg-stone-900 px-3 py-1.5 text-sm text-white">Save tax config</button>
          </form>
          <p className="mt-2 text-xs text-stone-500">
            A new configuration applies from now. Bills already frozen (payment requested or paid) never
            change. Tavessa does not claim tax compliance — confirm the rate and mode with your accountant.
          </p>
          {tax && tax.history.length > 0 && (
            <div className="mt-3">
              <h3 className="text-sm font-medium">History</h3>
              <ul className="mt-1 divide-y divide-stone-100 text-sm">
                {tax.history.map((c) => (
                  <li key={c.id} className="py-1">
                    {c.enabled ? `${c.label ?? "Tax"} ${(c.rateBps ?? 0) / 100}% (${c.mode})` : "Disabled"} ·{" "}
                    from {new Date(c.effectiveFrom).toLocaleString()}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {tax && tax.history.length === 0 && <EmptyState message="No tax configuration history yet." />}
        </section>
      </div>
    </div>
  );
}
