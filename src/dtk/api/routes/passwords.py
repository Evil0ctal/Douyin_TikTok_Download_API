"""Password hashing for console accounts.

argon2id with the library's current defaults, which track the OWASP guidance
doc 08 asks for. The parameters are encoded in the digest, so a hash written by
an older build still verifies and can be upgraded in place on the next
successful login.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from dtk.core.logging import get_logger

log = get_logger(__name__)

#: Short passwords are the one rule worth enforcing server-side. Everything
#: else (dictionary checks, rotation) belongs to the deployer, not to a tool
#: running on their own machine.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256

_hasher = PasswordHasher()

#: A real digest, verified against when the account does not exist, so that a
#: missing username costs the same as a wrong password. Hardcoding a string
#: here would risk it being rejected as malformed and returning early, which is
#: exactly the timing difference this is meant to remove; it is computed once
#: at import instead. Without it the login endpoint enumerates users.
_DUMMY_HASH = _hasher.hash("dtk-no-such-account")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-ish time verification that never raises on bad input.

    ``password_hash`` may be ``None`` when the account was not found; a dummy
    digest is verified instead so the failure costs the same as a real one.
    """
    candidate = password_hash or _DUMMY_HASH
    try:
        _hasher.verify(candidate, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, VerificationError):
        return True


__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "hash_password",
    "needs_rehash",
    "verify_password",
]
