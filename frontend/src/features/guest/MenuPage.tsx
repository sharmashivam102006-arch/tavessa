import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { Clock, MapPin, Phone, ReceiptText, Search, Star } from "lucide-react";

import type { MenuItem } from "@/shared/api/types";
import { useGuestMenu, useGuestRestaurant } from "@/features/guest/hooks/useGuest";
import { ZAIKA_RESTAURANT } from "@/features/guest/data/zaika-menu";
import { FoodImage } from "@/features/guest/components/FoodImage";
import { ItemDetailSheet } from "@/features/guest/components/ItemDetailSheet";
import { MenuItemCard } from "@/features/guest/components/MenuItemCard";
import { EmptyState, ErrorState } from "@/features/guest/components/states";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Guest landing + menu browsing.
 * - Hero with restaurant info, search, veg-only toggle.
 * - Sticky category bar with scroll-spy; one section per category.
 * - Item cards open the detail sheet.
 * - Session states: invalid/expired tokens get a full error screen;
 *   an unreachable session endpoint gets an honest banner while the
 *   labelled sample menu stays browsable. */
export function MenuPage() {
  const { tableToken = "" } = useParams<{ tableToken: string }>();
  const { menu, isSample } = useGuestMenu(tableToken);
  const { restaurant, errorKind } = useGuestRestaurant(tableToken);
  const [query, setQuery] = useState("");
  const [vegOnly, setVegOnly] = useState(false);
  const [activeCategory, setActiveCategory] = useState(menu.categories[0]?.id ?? "");
  const [selectedItem, setSelectedItem] = useState<MenuItem | null>(null);
  const sectionRefs = useRef<Record<string, HTMLElement | null>>({});

  const normalizedQuery = query.trim().toLowerCase();
  const visibleItems = useMemo(
    () =>
      menu.items.filter((item) => {
        if (vegOnly && !item.isVeg) return false;
        if (!normalizedQuery) return true;
        return (
          item.name.toLowerCase().includes(normalizedQuery) ||
          item.description.toLowerCase().includes(normalizedQuery)
        );
      }),
    [menu.items, vegOnly, normalizedQuery],
  );
  const visibleCategories = useMemo(
    () =>
      menu.categories.filter((cat) =>
        visibleItems.some((item) => item.categoryId === cat.id),
      ),
    [menu.categories, visibleItems],
  );

  // Scroll-spy: the category whose section top is nearest the
  // sticky bar becomes active.
  useEffect(() => {
    const onScroll = () => {
      let current = visibleCategories[0]?.id ?? "";
      for (const cat of visibleCategories) {
        const el = sectionRefs.current[cat.id];
        if (el && el.getBoundingClientRect().top <= 140) current = cat.id;
      }
      setActiveCategory(current);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [visibleCategories]);

  const scrollToCategory = (categoryId: string) => {
    setActiveCategory(categoryId);
    sectionRefs.current[categoryId]?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  if (errorKind === "invalid_token") {
    return (
      <ErrorState
        title="This QR code isn't valid"
        body="The table link may have been rotated or revoked. Please scan the QR code on your table again — or ask a member of staff for help."
        onRetry={() => window.location.reload()}
      />
    );
  }
  if (errorKind === "expired") {
    return (
      <ErrorState
        title="This table session has expired"
        body="Your session ended (decision D5 — session expiry — is still open). Please scan the QR code on your table again to start a new session."
        onRetry={() => window.location.reload()}
      />
    );
  }

  return (
    <div>
      {/* Hero */}
      <section className="overflow-hidden rounded-2xl bg-brand-900 text-white">
        <div className="relative h-44 sm:h-56">
          <FoodImage
            src="https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=1200&q=80&auto=format&fit=crop"
            alt="Inside Zaika restaurant"
            className="h-full w-full"
          />
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-5 pb-4 pt-10">
            <h1 className="text-3xl font-semibold tracking-tight">{restaurant.name}</h1>
            <p className="mt-0.5 text-sm text-stone-200">{restaurant.tagline ?? ZAIKA_RESTAURANT.tagline}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-5 py-3 text-xs text-stone-200">
          <span className="inline-flex items-center gap-1">
            <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
            {restaurant.rating ?? "—"} · {restaurant.cuisine ?? ZAIKA_RESTAURANT.cuisine}
          </span>
          <span className="inline-flex items-center gap-1">
            <Clock className="h-3.5 w-3.5" />
            {restaurant.openingHours ?? ZAIKA_RESTAURANT.openingHours}
          </span>
          <span className="inline-flex items-center gap-1">
            <MapPin className="h-3.5 w-3.5" />
            {restaurant.address ?? ZAIKA_RESTAURANT.address}
          </span>
          <span className="inline-flex items-center gap-1">
            <Phone className="h-3.5 w-3.5" />
            {restaurant.phone ?? ZAIKA_RESTAURANT.phone}
          </span>
        </div>
      </section>

      {/* Session / sample banners */}
      {errorKind === "not_connected" ? (
        <div className="mt-4 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <strong>Table not verified yet.</strong> The session endpoint (
          <code className="text-xs">GET /guest/t/:tableToken</code>) is not
          available — the backend adds it in Stage 3. You are browsing the
          sample menu; no table has been verified.
        </div>
      ) : null}
      {isSample ? (
        <div className="mt-4 rounded-xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-600">
          <strong>Sample menu.</strong> Demo data for Zaika — prices shown
          are for browsing only. The real menu loads from the backend in
          Stage 3, and final prices are always calculated by the backend.
        </div>
      ) : null}

      {/* Search + filters */}
      <div className="mt-5 flex gap-2">
        <label className="relative flex-1">
          <span className="sr-only">Search the menu</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search dishes — try “butter chicken”"
            className="w-full rounded-full border border-stone-300 bg-white py-2.5 pl-10 pr-4 text-sm outline-none focus:border-brand-600 focus:ring-1 focus:ring-brand-600"
          />
        </label>
        <button
          type="button"
          onClick={() => setVegOnly((v) => !v)}
          aria-pressed={vegOnly}
          className={cn(
            "shrink-0 rounded-full border px-3 py-2 text-sm font-medium",
            vegOnly
              ? "border-green-700 bg-green-700 text-white"
              : "border-stone-300 bg-white text-stone-700",
          )}
        >
          Veg only
        </button>
      </div>

      {/* Sticky category bar */}
      <nav
        aria-label="Menu categories"
        className="sticky top-[65px] z-30 -mx-4 mt-4 overflow-x-auto border-y border-stone-200 bg-stone-50/95 px-4 py-2 backdrop-blur"
      >
        <div className="flex gap-2">
          {menu.categories.map((cat) => (
            <button
              key={cat.id}
              type="button"
              onClick={() => scrollToCategory(cat.id)}
              className={cn(
                "shrink-0 rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors",
                activeCategory === cat.id
                  ? "bg-brand-700 text-white"
                  : "bg-white text-stone-700 ring-1 ring-stone-200 hover:ring-brand-600",
              )}
            >
              {cat.name}
            </button>
          ))}
        </div>
      </nav>

      {/* Category sections */}
      {visibleCategories.length === 0 ? (
        <div className="mt-6">
          <EmptyState
            title="No dishes match your search"
            body={`Nothing on the menu matches “${query}”${vegOnly ? " in vegetarian dishes" : ""}. Try a different dish name.`}
            action={
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setQuery("");
                  setVegOnly(false);
                }}
              >
                Clear search & filters
              </Button>
            }
          />
        </div>
      ) : (
        visibleCategories.map((cat) => (
          <section
            key={cat.id}
            id={`category-${cat.id}`}
            ref={(el) => {
              sectionRefs.current[cat.id] = el;
            }}
            className="scroll-mt-36 pt-6"
          >
            <h2 className="text-xl font-semibold tracking-tight">{cat.name}</h2>
            {cat.description ? (
              <p className="mt-0.5 text-sm text-stone-500">{cat.description}</p>
            ) : null}
            <div className="mt-3 space-y-3">
              {visibleItems
                .filter((item) => item.categoryId === cat.id)
                .map((item) => (
                  <MenuItemCard key={item.id} item={item} onOpen={setSelectedItem} />
                ))}
            </div>
          </section>
        ))
      )}

      {/* Quick links */}
      <div className="mt-8 flex flex-wrap gap-2">
        <Button variant="outline" size="sm" asChild>
          <Link to={`/t/${tableToken}/orders`}>
            <ReceiptText className="h-4 w-4" />
            Track my orders
          </Link>
        </Button>
        <Button variant="outline" size="sm" asChild>
          <Link to={`/t/${tableToken}/bill`}>View bill</Link>
        </Button>
      </div>

      <ItemDetailSheet item={selectedItem} onClose={() => setSelectedItem(null)} />
    </div>
  );
}
