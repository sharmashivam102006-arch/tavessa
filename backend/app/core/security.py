"""Security primitives.

Decision D-ARCH-6 (recorded in Stage 1): password hashing is Argon2id
via argon2-cffi. No bcrypt fallback is wired in — Argon2id is the one
algorithm, configured here, never MD5/SHA-1/plaintext.

JWT helpers land with auth in Stage 3; this module holds hashing now so
the choice is real code, not a comment.
"""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

import hashlib
import secrets

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id. Never store the input."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return True iff the password matches the stored hash."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # A malformed stored hash must never authenticate.
        return False


# --- QR table tokens (Stage 3) -------------------------------------------
# Opaque random tokens, hashed at rest with SHA-256. The raw token is
# shown exactly once (at creation, for QR printing) and never stored
# or logged.

def generate_table_token() -> str:
    """A fresh raw QR token (URL-safe, ~43 chars, 256 bits of entropy)."""
    return secrets.token_urlsafe(32)


def hash_table_token(raw_token: str) -> str:
    """SHA-256 hex of the raw token — the only form ever stored."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
