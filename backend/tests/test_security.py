"""Password hashing — Argon2id (decision D-ARCH-6)."""
from app.core.security import hash_password, verify_password


def test_hash_and_verify():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_malformed_hash_never_authenticates():
    assert verify_password("anything", "not-a-real-hash") is False
