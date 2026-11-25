/** Boot-time silent refresh — Stage 9 closure.
 *
 * Runs once per page load (StrictMode may double-invoke the effect
 * in development; `bootRefresh` dedupes that into a single network
 * call). Until it settles, the auth store stays "loading" and the
 * route guards wait instead of redirecting to /login — that is what
 * makes a page reload keep a signed-in user signed in.
 *
 * Rendered unconditionally in App, so the attempt also happens on
 * guest pages: a guest has no refresh cookie, so the call answers
 * 401 immediately and the store settles on "anonymous" with no
 * user-visible effect.
 */
import { useEffect } from "react";

import { bootRefresh } from "@/shared/api/client";

export function BootAuth() {
  useEffect(() => {
    void bootRefresh();
  }, []);
  return null;
}
