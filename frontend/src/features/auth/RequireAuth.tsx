/** Route guards — Stage 7.
 *
 * ``RequireAuth``: redirects to /login when unauthenticated.
 * ``RequireStaffAuth``: in development the staff/kitchen boards
 * keep their dev-key flow, so no login is required; in production
 * builds they require a login (the dev key is never sent in a
 * production build — see the *Api modules). */
import { Navigate, Outlet, useLocation } from "react-router";

import { useAuthStore } from "@/shared/stores/authStore";

export function RequireAuth() {
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.accessToken);
  const location = useLocation();
  if (!user || !token) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }
  return <Outlet />;
}

export function RequireStaffAuth() {
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.accessToken);
  const location = useLocation();
  if (!import.meta.env.DEV && (!user || !token)) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }
  return <Outlet />;
}
