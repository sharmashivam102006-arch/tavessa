import { useState } from "react";
import { useParams } from "react-router";
import { Bell, CheckCircle2, Droplets, HandPlatter, ShoppingBag } from "lucide-react";

import { ApiError } from "@/shared/api/client";
import { HELP_CALL_LABELS, type HelpCallType } from "@/shared/api/types";
import { requestHelpCall } from "@/features/guest/api/guestApi";
import { BackendNotConnected } from "@/features/guest/components/states";
import { Button } from "@/components/ui/button";

const HELP_TYPES: { type: HelpCallType; icon: typeof Bell; hint: string }[] = [
  { type: "waiter", icon: Bell, hint: "A waiter will come to your table." },
  { type: "water", icon: Droplets, hint: "A jug of water for the table." },
  { type: "plates", icon: HandPlatter, hint: "Extra plates, bowls or cutlery." },
  { type: "order_help", icon: ShoppingBag, hint: "Something about an order you placed." },
];

/** Waiter assistance — pick a type, add an optional note, send.
 * The request attempts the real POST /guest/help-calls. On failure
 * it shows the not-connected state and never claims the request
 * was sent. One open call per session is a backend rule (api-plan
 * § 5: a second call returns 409 with the existing call). */
export function HelpPage() {
  const { tableToken = "" } = useParams<{ tableToken: string }>();
  const [type, setType] = useState<HelpCallType>("waiter");
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const submit = async () => {
    setSending(true);
    setError(null);
    try {
      await requestHelpCall(tableToken, { type, note: note.trim() || undefined });
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "request_failed", "Could not reach the backend."));
    } finally {
      setSending(false);
    }
  };

  if (sent) {
    return (
      <div className="text-center">
        <CheckCircle2 className="mx-auto h-14 w-14 text-brand-700" />
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Help is on the way</h1>
        <p className="mx-auto mt-1 max-w-sm text-sm text-stone-600">
          Your request — “{HELP_CALL_LABELS[type]}” — was sent to the
          waiters. Someone will be with you shortly.
        </p>
        <Button
          variant="outline"
          className="mt-5"
          onClick={() => {
            setSent(false);
            setNote("");
          }}
        >
          Make another request
        </Button>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Need a hand?</h1>
      <p className="mt-1 text-sm text-stone-600">
        Tell us what you need and a waiter will come to your table.
      </p>

      <div className="mt-5 grid grid-cols-2 gap-3">
        {HELP_TYPES.map(({ type: t, icon: Icon, hint }) => (
          <button
            key={t}
            type="button"
            onClick={() => setType(t)}
            aria-pressed={type === t}
            className={`rounded-xl border p-4 text-left shadow-sm transition-colors ${
              type === t
                ? "border-brand-600 bg-brand-50 ring-1 ring-brand-600"
                : "border-stone-200 bg-white hover:border-brand-600"
            }`}
          >
            <Icon className="h-6 w-6 text-brand-700" />
            <p className="mt-2 text-sm font-semibold leading-snug">{HELP_CALL_LABELS[t]}</p>
            <p className="mt-0.5 text-xs leading-snug text-stone-500">{hint}</p>
          </button>
        ))}
      </div>

      <div className="mt-5">
        <label htmlFor="help-note" className="text-sm font-semibold">
          Add a note <span className="text-xs font-normal text-stone-500">Optional</span>
        </label>
        <textarea
          id="help-note"
          rows={2}
          maxLength={200}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. Two extra glasses, please"
          className="mt-2 w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand-600 focus:ring-1 focus:ring-brand-600"
        />
      </div>

      {error ? (
        <div className="mt-4">
          <BackendNotConnected
            endpoint="POST /api/v1/guest/help-calls"
            stage="Stage 3 (backend APIs & database integration)"
            what="Sending this help request"
          >
            <p className="mt-2 text-sm text-amber-800">
              The backend answered {error.status} ({error.code}). Your
              request was not sent — please flag a waiter down, or retry
              once the backend is connected.
            </p>
            <Button variant="outline" size="sm" className="mt-3" onClick={submit} disabled={sending}>
              Retry sending
            </Button>
          </BackendNotConnected>
        </div>
      ) : (
        <Button size="lg" className="mt-5 w-full" onClick={submit} disabled={sending}>
          {sending ? "Sending…" : `Send request — ${HELP_CALL_LABELS[type]}`}
        </Button>
      )}
      <p className="mt-3 text-center text-xs text-stone-500">
        One open request per table — the backend will tell you if a request
        is already open.
      </p>
    </div>
  );
}
