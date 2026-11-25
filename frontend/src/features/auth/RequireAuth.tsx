/** Route guards — Stage 7, boot-time restore added in the Stage 9 closure.
 *
 * ``RequireAuth``: redirects to /login when unauthenticated.
 * ``RequireStaffAuth``: in development the staff/kitchen boards
 * keep their dev-key flow, so no login is required; in production
 * builds they require a login (the dev key is never sent in a
 * production build — see the *Api modules).
 *
 * Both guards WAIT while the auth store is "loading" (the boot-time
 * silent refresh has not settled yet) instead of redirecting
 * immediately — otherwise a reload would flash the user to /login
 * before the refresh cookie could restore the session. */
import { Navigate, Outlet, useLocation } from "react-router";

import { useAuthStore } from "@/shared/stores/authStore";

function BootLoading() {
  return (
    <div className="mx-auto max-w-md p-6 text-center text-sm text-stone-600" role="status">
      Signing you in…
    </div>
  );
}

export function RequireAuth() {
  const status = useAuthStore((s) => s.status);
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.accessToken);
  const location = useLocation();
  if (status === "loading") {
    return <BootLoading />;
  }
  if (!user || !token) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }
  return <Outlet />;
}

export function RequireStaffAuth() {
  const status = useAuthStore((s) => s.status);
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.accessToken);
  const location = useLocation();
  if (status === "loading") {
    return <BootLoading />;
  }
  if (!import.meta.env.DEV && (!user || !token)) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }
  return <Outlet />;
}
