"""Security primitives.

Decision D-ARCH-6 (recorded in Stage 1): password hashing is Argon2id
via argon2-cffi. No bcrypt fallback is wired in — Argon2id is the one
algorithm, configured here, never MD5/SHA-1/plaintext.

JWT helpers land with auth in Stage 3; this module holds hashing now so
the choice is real code, not a comment.
"""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

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
