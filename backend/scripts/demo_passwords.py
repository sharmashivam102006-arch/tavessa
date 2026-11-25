"""Demo passwords for the local verification scripts (Stage 9 closure).

The verification scripts create their own demo users in-run (owner via
the bootstrap CLI, waiter/kitchen via the admin API), so the passwords
only have to match within a single run — they are never real
credentials and must never be committed.

Rules (Shivam's Stage 9 review decision 1):
- Read ``TAVESSA_DEMO_OWNER_PASSWORD`` / ``TAVESSA_DEMO_WAITER_PASSWORD``
  / ``TAVESSA_DEMO_KITCHEN_PASSWORD`` from the environment when set,
  e.g. to reproduce a run or to share one password between two scripts
  (``run_e2e_compose.sh`` bootstraps the owner and then runs
  ``verify_stage9_e2e.py --external`` — both must use the same owner
  password, so the wrapper exports it).
- When unset, generate a fresh random password per run with
  ``secrets.token_urlsafe``. There is deliberately NO hardcoded
  fallback default: a hardcoded value would just re-introduce the
  problem this module exists to fix.
- Never print or log the returned value. The scripts log check
  results only.

The historical hardcoded values remain in
public git history — that is accepted and documented, not fixed:
history is not rewritten. See ``docs/development-roadmap.md``
(Stage 9 closure note).
"""

from __future__ import annotations

import os
import secrets

ENV_OWNER = "TAVESSA_DEMO_OWNER_PASSWORD"
ENV_WAITER = "TAVESSA_DEMO_WAITER_PASSWORD"
ENV_KITCHEN = "TAVESSA_DEMO_KITCHEN_PASSWORD"


def demo_password(env_name: str) -> str:
    """Return the demo password for ``env_name``.

    Environment value wins; otherwise a fresh random password is
    generated for this run. The generated value is >= 8 characters
    (the bootstrap CLI minimum) and is only ever used to create
    throwaway users in an ephemeral database.
    """
    value = os.environ.get(env_name)
    if value:
        return value
    return secrets.token_urlsafe(18)
