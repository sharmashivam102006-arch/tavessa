import { Link } from "react-router";
import { ChefHat, LayoutDashboard, QrCode, Users } from "lucide-react";

import { Button } from "@/components/ui/button";

const INTERFACES = [
  {
    to: "/t/demo",
    icon: QrCode,
    title: "Guest",
    text: "Scan a table QR code, browse Zaika's menu, order and track — Stage 2.",
  },
  {
    to: "/staff",
    icon: Users,
    title: "Waiter / Staff",
    text: "Floor map, orders, help calls and cash collection — Stage 5.",
  },
  {
    to: "/kitchen",
    icon: ChefHat,
    title: "Kitchen Display",
    text: "Live tickets by station, with timers — Stage 4.",
  },
  {
    to: "/admin",
    icon: LayoutDashboard,
    title: "Owner / Admin",
    text: "Occupancy, sales, cash, menu and staff — Stage 7.",
  },
];

/** Stage 1 brand screen. Not a dashboard — the four interfaces are
 * built in their stages; this page shows the brand and the route map. */
export function HomePage() {
  return (
    <div>
      <p className="text-sm font-medium uppercase tracking-widest text-brand-700">
        Zaika · Demo restaurant
      </p>
      <h1 className="mt-2 text-4xl font-semibold tracking-tight sm:text-5xl">
        Tavessa
      </h1>
      <p className="mt-3 max-w-xl text-lg text-stone-600">
        Every Table. Every Order. In Sync.
      </p>
      <p className="mt-4 max-w-xl text-stone-600">
        One system for guests, waiters, the kitchen and the owner — sharing
        the same orders, tables and bills in real time. Cash payments only
        in v1.
      </p>
      <div className="mt-6 flex gap-3">
        <Button asChild>
          <Link to="/t/demo">Open the guest view</Link>
        </Button>
        <Button variant="outline" asChild>
          <Link to="/admin">Owner dashboard</Link>
        </Button>
      </div>

      <div className="mt-10 grid gap-4 sm:grid-cols-2">
        {INTERFACES.map(({ to, icon: Icon, title, text }) => (
          <Link
            key={to}
            to={to}
            className="rounded-lg border border-stone-200 bg-white p-5 transition-colors hover:border-brand-600"
          >
            <Icon className="h-6 w-6 text-brand-700" />
            <h2 className="mt-3 font-semibold">{title}</h2>
            <p className="mt-1 text-sm text-stone-600">{text}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}
