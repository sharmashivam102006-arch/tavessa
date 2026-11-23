"""Rate limiting (slowapi, in-process, per IP).

Wired in ``app.main`` (app.state.limiter + exception handler). Only two
routes are limited in Stage 3 — the QR session-open (brute-force guard)
and order placement — with generous caps so legitimate use is never
throttled; the caps exist to blunt abuse, not to police guests.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=[])

SESSION_OPEN_LIMIT = "120/minute"
ORDER_PLACE_LIMIT = "60/minute"
# Kitchen status transitions (Stage 4): same generous cap as order
# placement — it blunts abuse, it does not police the kitchen.
KITCHEN_PATCH_LIMIT = "60/minute"
# Staff actions (Stage 5): serving + help-call ack/resolve.
STAFF_PATCH_LIMIT = "60/minute"
