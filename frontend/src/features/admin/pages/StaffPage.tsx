/** Admin staff management — Stage 7 § A. List, create (role +
 * branch + permission flags), deactivate and reset-password, each
 * with a confirmation. New accounts must change their password on
 * first login. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/shared/api/client";
import {
  createUser,
  deactivateUser,
  fetchUsers,
  resetUserPassword,
  updateUser,
} from "@/features/admin/api/adminApi";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/features/admin/components/AdminBits";
import { useAuthStore } from "@/shared/stores/authStore";

const ROLES = ["owner", "manager", "waiter", "kitchen"];

export function StaffPage() {
  const me = useAuthStore((s) => s.user);
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("waiter");
  const [confirm, setConfirm] = useState<
    | { kind: "deactivate"; userId: string; label: string }
    | { kind: "reset"; userId: string; label: string }
    | null
  >(null);
  const [resetPassword, setResetPassword] = useState("");

  const query = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => fetchUsers(1),
    retry: false,
  });
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
  const fail = (error: unknown) =>
    setMessage(error instanceof ApiError ? error.message : "Something went wrong.");

  const create = useMutation({
    mutationFn: () => createUser({ email, password, role }),
    onSuccess: (user) => {
      setMessage(`Created ${user.email} (${user.role}). They must change their password on first login.`);
      setEmail("");
      setPassword("");
      invalidate();
    },
    onError: fail,
  });
  const deactivate = useMutation({
    mutationFn: (userId: string) => deactivateUser(userId),
    onSuccess: () => {
      setMessage("User deactivated — their sessions were revoked immediately.");
      setConfirm(null);
      invalidate();
    },
    onError: fail,
  });
  const reset = useMutation({
    mutationFn: ({ userId, pw }: { userId: string; pw: string }) =>
      resetUserPassword(userId, pw),
    onSuccess: () => {
      setMessage("Password reset — the user must change it on next login.");
      setConfirm(null);
      setResetPassword("");
      invalidate();
    },
    onError: fail,
  });
  const setFlag = useMutation({
    mutationFn: ({ userId, flag, value }: { userId: string; flag: string; value: boolean }) =>
      updateUser(userId, { [flag]: value }),
    onSuccess: invalidate,
    onError: fail,
  });

  const users = query.data?.data ?? [];

  return (
    <div>
      <PageHeader title="Staff" sub="Accounts, roles and permissions. Only the owner can create owner or manager accounts." />
      {message && (
        <p role="status" className="mb-3 rounded border border-stone-300 bg-white p-3 text-sm">
          {message}
        </p>
      )}
      <form
        className="mb-6 flex flex-wrap gap-2 rounded-lg border border-stone-200 bg-white p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (email && password.length >= 8) create.mutate();
        }}
      >
        <input
          aria-label="New user email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          className="rounded border border-stone-300 px-2 py-1.5 text-sm"
        />
        <input
          aria-label="Initial password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Initial password (min 8)"
          className="rounded border border-stone-300 px-2 py-1.5 text-sm"
        />
        <select
          aria-label="Role"
          value={role}
          onChange={(e) => setRole(e.target.value)}
          className="rounded border border-stone-300 px-2 py-1.5 text-sm"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <button className="rounded bg-stone-900 px-3 py-1.5 text-sm text-white">Create user</button>
      </form>
      {query.isPending && <LoadingState />}
      {query.isError && <ErrorState message="Could not load staff." onRetry={() => void query.refetch()} />}
      {query.data && users.length === 0 && <EmptyState message="No staff accounts yet." />}
      {users.length > 0 && (
        <table className="w-full rounded-lg border border-stone-200 bg-white text-sm">
          <thead>
            <tr className="border-b border-stone-200 text-left text-stone-500">
              <th className="px-3 py-2">Email</th>
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Permissions</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-stone-100">
                <td className="px-3 py-2">
                  {u.email}
                  {u.mustChangePassword && (
                    <span className="block text-xs text-amber-700">Must change password</span>
                  )}
                </td>
                <td className="px-3 py-2 capitalize">{u.role}</td>
                <td className="px-3 py-2">
                  {u.role === "manager" ? (
                    <span className="flex flex-wrap gap-2 text-xs">
                      {(
                        [
                          ["canManageStaff", "staff"],
                          ["canManageSettings", "settings"],
                          ["canRefund", "refund"],
                          ["canVoid", "void"],
                        ] as const
                      ).map(([flag, label]) => (
                        <label key={flag} className="flex items-center gap-1">
                          <input
                            type="checkbox"
                            checked={Boolean(u[flag])}
                            onChange={(e) =>
                              setFlag.mutate({ userId: u.id, flag, value: e.target.checked })
                            }
                          />
                          {label}
                        </label>
                      ))}
                    </span>
                  ) : (
                    <span className="text-xs text-stone-500">
                      {u.role === "owner" ? "All permissions" : "—"}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">{u.isActive ? "Active" : "Deactivated"}</td>
                <td className="px-3 py-2">
                  <span className="flex gap-1">
                    <button
                      onClick={() =>
                        setConfirm({ kind: "reset", userId: u.id, label: u.email })
                      }
                      className="rounded border px-2 py-1 text-xs"
                    >
                      Reset password
                    </button>
                    {u.isActive && u.id !== me?.id && (
                      <button
                        onClick={() =>
                          setConfirm({ kind: "deactivate", userId: u.id, label: u.email })
                        }
                        className="rounded border border-red-300 px-2 py-1 text-xs text-red-700"
                      >
                        Deactivate
                      </button>
                    )}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {confirm && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/40" role="dialog" aria-label="Confirm staff action">
          <div className="w-full max-w-md rounded-lg bg-white p-5">
            <h2 className="text-lg font-semibold">
              {confirm.kind === "deactivate" ? `Deactivate ${confirm.label}?` : `Reset password for ${confirm.label}?`}
            </h2>
            <p className="mt-1 text-sm text-stone-600">
              {confirm.kind === "deactivate"
                ? "Their sessions are revoked immediately and they can no longer log in."
                : "Their sessions are revoked and they must set a new password on next login."}
            </p>
            {confirm.kind === "reset" && (
              <label className="mt-4 block text-sm font-medium">
                New password (min 8 characters)
                <input
                  type="password"
                  value={resetPassword}
                  onChange={(e) => setResetPassword(e.target.value)}
                  className="mt-1 w-full rounded border border-stone-300 px-2 py-1.5"
                />
              </label>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setConfirm(null)} className="rounded border px-3 py-1.5 text-sm">
                Cancel
              </button>
              <button
                disabled={
                  confirm.kind === "reset"
                    ? resetPassword.length < 8
                    : deactivate.isPending
                }
                onClick={() =>
                  confirm.kind === "deactivate"
                    ? deactivate.mutate(confirm.userId)
                    : reset.mutate({ userId: confirm.userId, pw: resetPassword })
                }
                className="rounded bg-red-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
