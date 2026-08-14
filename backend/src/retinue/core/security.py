"""AuthN primitives (§16, D18).

Passwords: Argon2id (t=3, m=64 MiB, p=4) over a peppered digest.
Access tokens: EdDSA (Ed25519) JWTs, 15-minute TTL, claims {sub, role, jti, sv}.
Refresh tokens / API keys: opaque high-entropy strings, BLAKE3-hashed at rest.
"""

import secrets
import stat
import uuid
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from retinue.core.crypto import pepper_password
from retinue.core.ids import uuid7
from retinue.core.timeutil import now_ms

ISSUER = "retinue"
REFRESH_PREFIX = "rtr_"
API_KEY_PREFIX = "rtn_"


class PasswordService:
    def __init__(self, master_secret: str) -> None:
        self._secret = master_secret
        self._hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)

    def hash(self, password: str) -> str:
        return self._hasher.hash(pepper_password(self._secret, password))

    def verify(self, stored_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(stored_hash, pepper_password(self._secret, password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, stored_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(stored_hash)
        except InvalidHashError:
            return True


def _load_or_create_signing_key(path: Path) -> Ed25519PrivateKey:
    if path.is_file():
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError(f"{path} is not an Ed25519 private key")
        return loaded
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return key


class TokenService:
    """Ed25519 JWT mint/verify. The signing key persists in the data dir."""

    def __init__(self, key_path: Path, access_ttl_s: int) -> None:
        self.access_ttl_s = access_ttl_s
        self._private = _load_or_create_signing_key(key_path)
        self._public = self._private.public_key()

    def make_access_token(self, *, user_id: uuid.UUID, role: str, session_version: int) -> str:
        now_s = now_ms() // 1000
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "sub": str(user_id),
            "role": role,
            "sv": session_version,
            "jti": uuid7().hex,
            "iat": now_s,
            "exp": now_s + self.access_ttl_s,
        }
        return jwt.encode(claims, self._private, algorithm="EdDSA")

    def decode_access_token(self, token: str) -> dict[str, Any]:
        """Raises jwt.InvalidTokenError subclasses on any failure."""
        return jwt.decode(
            token,
            self._public,
            algorithms=["EdDSA"],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub"]},
        )


def new_refresh_token() -> str:
    return REFRESH_PREFIX + secrets.token_urlsafe(32)


def new_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)
