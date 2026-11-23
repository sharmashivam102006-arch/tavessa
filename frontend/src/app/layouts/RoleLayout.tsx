import { Outlet } from "react-router";

/** Minimal role shell for Stage 1.
 * Each interface gets its own full layout in its stage (Guest Stage 2,
 * Kitchen Stage 4, Staff Stage 5, Admin Stage 7) — this shell only
 * proves the route exists and names the interface. */
export function RoleLayout({ title, hint }: { title: string; hint: string }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-1 text-sm text-stone-600">{hint}</p>
      <div className="mt-6">
        <Outlet />
      </div>
    </div>
  );
}
