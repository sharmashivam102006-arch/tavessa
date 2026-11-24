/** Admin layout — Stage 7: sidebar navigation, branding, user
 * profile + logout. Navigation is role-aware (display-only; the
 * server enforces every permission). */
import { NavLink, Outlet, useNavigate } from "react-router";

import { logout } from "@/features/admin/api/adminApi";
import { canUse, useAuthStore } from "@/shared/stores/authStore";

const NAV: { to: string; label: string; capability: string }[] = [
  { to: "/admin", label: "Overview", capability: "view_dashboard" },
  { to: "/admin/orders", label: "Orders", capability: "view_dashboard" },
  { to: "/admin/menu", label: "Menu", capability: "manage_menu" },
  { to: "/admin/staff", label: "Staff", capability: "manage_staff" },
  { to: "/admin/tables", label: "Tables", capability: "manage_settings" },
  { to: "/admin/bills", label: "Bills", capability: "view_dashboard" },
  { to: "/admin/cash", label: "Cash reports", capability: "view_financials" },
  { to: "/admin/refunds", label: "Refunds", capability: "view_financials" },
  { to: "/admin/audit-logs", label: "Audit log", capability: "view_dashboard" },
  { to: "/admin/settings", label: "Settings", capability: "manage_settings" },
];

export function AdminLayout() {
  const user = useAuthStore((s) => s.user);
  const clearAuth = useAuthStore((s) => s.clearAuth);
  const navigate = useNavigate();

  const onLogout = async () => {
    await logout();
    clearAuth();
    navigate("/login");
  };

  return (
    <div className="flex min-h-screen bg-stone-100">
      <aside className="w-56 shrink-0 bg-stone-900 p-4 text-stone-100">
        <div className="mb-6">
          <p className="text-lg font-bold tracking-tight">Tavessa</p>
          <p className="text-xs text-stone-400">Owner / Admin</p>
        </div>
        <nav className="flex flex-col gap-1">
          {NAV.filter((item) => canUse(user, item.capability)).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/admin"}
              className={({ isActive }) =>
                `rounded px-3 py-2 text-sm ${
                  isActive ? "bg-stone-700 text-white" : "text-stone-300 hover:bg-stone-800"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-8 border-t border-stone-700 pt-4 text-xs">
          <p className="font-medium text-stone-200">{user?.email}</p>
          <p className="text-stone-400 capitalize">{user?.role}</p>
          <button
            onClick={onLogout}
            className="mt-3 rounded border border-stone-600 px-3 py-1.5 text-stone-200 hover:bg-stone-800"
          >
            Log out
          </button>
        </div>
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
