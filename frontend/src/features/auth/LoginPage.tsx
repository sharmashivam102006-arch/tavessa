/** Login — Stage 7. Email + password, calls /auth/login, stores
 * the access token in memory only (Zustand). The refresh cookie is
 * handled by the browser. After login the user lands by role:
 * owner/manager → /admin, waiter → /staff, kitchen → /kitchen.
 * An account flagged mustChangePassword is forced through the
 * change-password form before it can proceed. */
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import { ApiError } from "@/shared/api/client";
import {
  changePassword,
  login,
} from "@/features/admin/api/adminApi";
import {
  landingRouteFor,
  useAuthStore,
} from "@/shared/stores/authStore";

export function LoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const setAuth = useAuthStore((s) => s.setAuth);
  const setUser = useAuthStore((s) => s.setUser);
  const sessionExpired = useAuthStore((s) => s.sessionExpired);
  const status = useAuthStore((s) => s.status);
  const authedUser = useAuthStore((s) => s.user);
  const authedToken = useAuthStore((s) => s.accessToken);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [needsChange, setNeedsChange] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const onLogin = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const result = await login(email, password);
      setAuth(result.accessToken, result.user);
      if (result.user.mustChangePassword) {
        setNeedsChange(true);
        return;
      }
      navigate(landingRouteFor(result.user.role), { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not sign in. Please try again.",
      );
    } finally {
      setPending(false);
    }
  };

  const onChangePassword = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) {
      setError("The new password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("The new passwords do not match.");
      return;
    }
    setPending(true);
    try {
      const user = await changePassword(password, newPassword);
      setUser(user);
      navigate(landingRouteFor(user.role), { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not change the password.",
      );
    } finally {
      setPending(false);
    }
  };

  // Stage 9 closure: a boot-time silent refresh may already have
  // restored the session before the user reaches this page (e.g.
  // they reloaded on /login, or navigated here while signed in).
  // Send them on to their board instead of showing the form again.
  // Accounts flagged mustChangePassword still see the form: the
  // change-password flow must not be bypassed by a restore.
  if (
    status === "authenticated" &&
    authedUser &&
    authedToken &&
    !authedUser.mustChangePassword
  ) {
    return <Navigate to={landingRouteFor(authedUser.role)} replace />;
  }

  return (
    <div className="mx-auto max-w-md rounded-lg border border-stone-200 bg-white p-6">
      <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
      <p className="mt-1 text-sm text-stone-600">
        Staff and owner sign-in for Tavessa.
      </p>
      {sessionExpired && (
        <p role="status" className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          Your session expired. Please sign in again.
        </p>
      )}
      {params.get("next") && (
        <p className="mt-3 text-sm text-stone-600">Sign in to continue.</p>
      )}
      {error && (
        <p role="alert" className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </p>
      )}
      {!needsChange ? (
        <form onSubmit={onLogin} className="mt-4 space-y-3">
          <label className="block text-sm font-medium">
            Email
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded border border-stone-300 px-2 py-2"
              required
            />
          </label>
          <label className="block text-sm font-medium">
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded border border-stone-300 px-2 py-2"
              required
            />
          </label>
          <button
            disabled={pending}
            className="w-full rounded bg-stone-900 px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            {pending ? "Signing in…" : "Sign in"}
          </button>
        </form>
      ) : (
        <form onSubmit={onChangePassword} className="mt-4 space-y-3">
          <p className="text-sm text-stone-600">
            You must change your password before continuing.
          </p>
          <label className="block text-sm font-medium">
            New password
            <input
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="mt-1 w-full rounded border border-stone-300 px-2 py-2"
              required
            />
          </label>
          <label className="block text-sm font-medium">
            Confirm new password
            <input
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="mt-1 w-full rounded border border-stone-300 px-2 py-2"
              required
            />
          </label>
          <button
            disabled={pending}
            className="w-full rounded bg-stone-900 px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            {pending ? "Saving…" : "Change password and continue"}
          </button>
        </form>
      )}
    </div>
  );
}
