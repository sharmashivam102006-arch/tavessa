/** Admin menu management — Stage 7 § E. Categories (create +
 * reorder), items (create, price, availability toggle, variants
 * and add-ons). Price changes never rewrite old bills — the
 * server snapshots prices at order/bill time. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api/client";
import {
  createAddOn,
  createCategory,
  createMenuItem,
  createVariant,
  fetchCategories,
  fetchMenuItems,
  reorderCategories,
  updateMenuItem,
} from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/features/admin/components/AdminBits";
import { formatINR } from "@/shared/utils/money";

export function MenuPage() {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [newCategory, setNewCategory] = useState("");
  const [newItemName, setNewItemName] = useState("");
  const [newItemPrice, setNewItemPrice] = useState("");
  const [newItemCategory, setNewItemCategory] = useState("");
  const [editingPrice, setEditingPrice] = useState<Record<string, string>>({});

  const categoriesQuery = useQuery({
    queryKey: ["admin-categories"],
    queryFn: fetchCategories,
    retry: false,
  });
  const itemsQuery = useQuery({
    queryKey: ["admin-menu-items"],
    queryFn: () => fetchMenuItems(1),
    retry: false,
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["admin-categories"] });
    void queryClient.invalidateQueries({ queryKey: ["admin-menu-items"] });
  };
  const fail = (error: unknown) =>
    setMessage(error instanceof ApiError ? error.message : "Something went wrong.");

  const addCategory = useMutation({
    mutationFn: (name: string) => createCategory(name),
    onSuccess: () => {
      setNewCategory("");
      setMessage("Category created.");
      invalidate();
    },
    onError: fail,
  });
  const addItem = useMutation({
    mutationFn: () =>
      createMenuItem({
        categoryId: newItemCategory,
        name: newItemName,
        pricePaise: Math.round(Number(newItemPrice) * 100),
      }),
    onSuccess: () => {
      setNewItemName("");
      setNewItemPrice("");
      setMessage("Menu item created.");
      invalidate();
    },
    onError: fail,
  });
  const toggleItem = useMutation({
    mutationFn: ({ id, isAvailable }: { id: string; isAvailable: boolean }) =>
      updateMenuItem(id, { isAvailable }),
    onSuccess: (item) => {
      setMessage(
        item.isAvailable
          ? `${item.name} is available again.`
          : `${item.name} is unavailable — it will not be orderable for new orders.`,
      );
      invalidate();
    },
    onError: fail,
  });
  const savePrice = useMutation({
    mutationFn: ({ id, price }: { id: string; price: number }) =>
      updateMenuItem(id, { pricePaise: price }),
    onSuccess: (item) => {
      setMessage(`Price updated for ${item.name}. Existing bills are unchanged.`);
      invalidate();
    },
    onError: fail,
  });
  const moveCategory = useMutation({
    mutationFn: ({ ids }: { ids: string[] }) => reorderCategories(ids),
    onSuccess: () => {
      setMessage("Category order saved.");
      invalidate();
    },
    onError: fail,
  });

  const categories = categoriesQuery.data ?? [];
  const items = itemsQuery.data?.data ?? [];

  return (
    <div>
      <PageHeader title="Menu" sub="Categories, items, prices, availability, variants and add-ons." />
      {message && (
        <p role="status" className="mb-3 rounded border border-stone-300 bg-white p-3 text-sm">
          {message}
        </p>
      )}
      {(categoriesQuery.isPending || itemsQuery.isPending) && <LoadingState />}
      {(categoriesQuery.isError || itemsQuery.isError) && (
        <ErrorState message="Could not load the menu." onRetry={invalidate} />
      )}
      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        <section className="rounded-lg border border-stone-200 bg-white p-4">
          <h2 className="font-semibold">Categories</h2>
          {categories.length === 0 ? (
            <EmptyState message="No categories yet." />
          ) : (
            <ul className="mt-2 divide-y divide-stone-100">
              {categories.map((c, idx) => (
                <li key={c.id} className="flex items-center justify-between py-2 text-sm">
                  <span>
                    {c.name} <span className="text-stone-500">({c.itemCount})</span>
                  </span>
                  <span className="flex gap-1">
                    <button
                      disabled={idx === 0}
                      aria-label={`Move ${c.name} up`}
                      onClick={() => {
                        const ids = categories.map((x) => x.id);
                        [ids[idx - 1], ids[idx]] = [ids[idx], ids[idx - 1]];
                        moveCategory.mutate({ ids });
                      }}
                      className="rounded border px-2 py-0.5 disabled:opacity-40"
                    >
                      ↑
                    </button>
                    <button
                      disabled={idx === categories.length - 1}
                      aria-label={`Move ${c.name} down`}
                      onClick={() => {
                        const ids = categories.map((x) => x.id);
                        [ids[idx], ids[idx + 1]] = [ids[idx + 1], ids[idx]];
                        moveCategory.mutate({ ids });
                      }}
                      className="rounded border px-2 py-0.5 disabled:opacity-40"
                    >
                      ↓
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
          <form
            className="mt-4 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (newCategory.trim()) addCategory.mutate(newCategory.trim());
            }}
          >
            <input
              aria-label="New category name"
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value)}
              placeholder="New category"
              className="min-w-0 flex-1 rounded border border-stone-300 px-2 py-1.5 text-sm"
            />
            <button className="rounded bg-stone-900 px-3 py-1.5 text-sm text-white">Add</button>
          </form>
        </section>
        <section className="rounded-lg border border-stone-200 bg-white p-4">
          <h2 className="font-semibold">Items</h2>
          <form
            className="mt-3 flex flex-wrap gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (newItemCategory && newItemName.trim() && Number(newItemPrice) > 0) {
                addItem.mutate();
              }
            }}
          >
            <select
              aria-label="Category for new item"
              value={newItemCategory}
              onChange={(e) => setNewItemCategory(e.target.value)}
              className="rounded border border-stone-300 px-2 py-1.5 text-sm"
            >
              <option value="">Category…</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <input
              aria-label="New item name"
              value={newItemName}
              onChange={(e) => setNewItemName(e.target.value)}
              placeholder="Item name"
              className="rounded border border-stone-300 px-2 py-1.5 text-sm"
            />
            <input
              aria-label="New item price in rupees"
              value={newItemPrice}
              onChange={(e) => setNewItemPrice(e.target.value)}
              placeholder="Price ₹"
              inputMode="decimal"
              className="w-24 rounded border border-stone-300 px-2 py-1.5 text-sm"
            />
            <button className="rounded bg-stone-900 px-3 py-1.5 text-sm text-white">Add item</button>
          </form>
          {items.length === 0 ? (
            <div className="mt-4">
              <EmptyState message="No menu items yet." />
            </div>
          ) : (
            <table className="mt-4 w-full text-sm">
              <thead>
                <tr className="border-b border-stone-200 text-left text-stone-500">
                  <th className="px-2 py-2">Item</th>
                  <th className="px-2 py-2">Category</th>
                  <th className="px-2 py-2">Price</th>
                  <th className="px-2 py-2">Available</th>
                  <th className="px-2 py-2">Variants / add-ons</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className="border-b border-stone-100 align-top">
                    <td className="px-2 py-2 font-medium">
                      {item.name}
                      <span className="block text-xs font-normal text-stone-500">
                        {item.isVeg ? "Veg" : "Non-veg"} · {item.station}
                      </span>
                    </td>
                    <td className="px-2 py-2">{item.categoryName}</td>
                    <td className="px-2 py-2">
                      <span className="flex items-center gap-1">
                        <input
                          aria-label={`Price for ${item.name} in rupees`}
                          value={
                            editingPrice[item.id] ??
                            String((item.pricePaise / 100).toFixed(2))
                          }
                          onChange={(e) =>
                            setEditingPrice((prev) => ({ ...prev, [item.id]: e.target.value }))
                          }
                          inputMode="decimal"
                          className="w-20 rounded border border-stone-300 px-1.5 py-1 text-sm"
                        />
                        <button
                          onClick={() =>
                            savePrice.mutate({
                              id: item.id,
                              price: Math.round(Number(editingPrice[item.id] ?? item.pricePaise / 100) * 100),
                            })
                          }
                          className="rounded border px-2 py-1 text-xs"
                        >
                          Save
                        </button>
                      </span>
                      <span className="block text-xs text-stone-500">{formatINR(item.pricePaise)}</span>
                    </td>
                    <td className="px-2 py-2">
                      <button
                        onClick={() =>
                          toggleItem.mutate({ id: item.id, isAvailable: !item.isAvailable })
                        }
                        className={`rounded px-2 py-1 text-xs ${
                          item.isAvailable ? "bg-green-100 text-green-800" : "bg-stone-200 text-stone-700"
                        }`}
                      >
                        {item.isAvailable ? "Available" : "Unavailable"}
                      </button>
                    </td>
                    <td className="px-2 py-2 text-xs text-stone-600">
                      {item.variants.length > 0 && (
                        <span className="block">Variants: {item.variants.map((v) => v.name).join(", ")}</span>
                      )}
                      {item.addOns.length > 0 && (
                        <span className="block">Add-ons: {item.addOns.map((a) => a.name).join(", ")}</span>
                      )}
                      {item.variants.length === 0 && item.addOns.length === 0 && "—"}
                      <span className="mt-1 flex gap-1">
                        <button
                          onClick={() => {
                            const name = window.prompt("Variant name?");
                            const delta = window.prompt("Price change in rupees (e.g. 50, or -20)?", "0");
                            if (name && delta !== null) {
                              void createVariant(item.id, name, Math.round(Number(delta) * 100)).then(invalidate);
                            }
                          }}
                          className="rounded border px-1.5 py-0.5"
                        >
                          + Variant
                        </button>
                        <button
                          onClick={() => {
                            const name = window.prompt("Add-on name?");
                            const price = window.prompt("Add-on price in rupees?", "0");
                            if (name && price !== null) {
                              void createAddOn(item.id, name, Math.round(Number(price) * 100)).then(invalidate);
                            }
                          }}
                          className="rounded border px-1.5 py-0.5"
                        >
                          + Add-on
                        </button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}
