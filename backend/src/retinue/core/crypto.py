"""Secrets-at-rest crypto (§16, D19).

AES-256-GCM envelope encryption with per-row nonces. Purpose-scoped keys are
derived from the single `RETINUE_SECRET` master secret via HKDF-SHA256, so the
credential-encryption key and the password pepper are independent even though
one secret configures both.
"""

import hashlib
import hmac
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NONCE_SIZE = 12


def derive_key(master_secret: str, purpose: str, length: int = 32) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=b"retinue.v1",
        info=purpose.encode("utf-8"),
    )
    return hkdf.derive(master_secret.encode("utf-8"))


class SecretBox:
    """AES-256-GCM sealed box bound to one purpose-derived key."""

    def __init__(self, master_secret: str, purpose: str = "credentials") -> None:
        self._aead = AESGCM(derive_key(master_secret, f"aes-gcm:{purpose}"))

    def encrypt(self, plaintext: bytes, aad: bytes | None = None) -> tuple[bytes, bytes]:
        """Returns (ciphertext, nonce)."""
        nonce = os.urandom(NONCE_SIZE)
        return self._aead.encrypt(nonce, plaintext, aad), nonce

    def decrypt(self, ciphertext: bytes, nonce: bytes, aad: bytes | None = None) -> bytes:
        return self._aead.decrypt(nonce, ciphertext, aad)


def pepper_password(master_secret: str, password: str) -> bytes:
    """HMAC the password with a derived pepper key before Argon2 hashing (§16).

    The stored Argon2 hash is useless without the server-side secret.
    """
    key = derive_key(master_secret, "password-pepper")
    return hmac.new(key, password.encode("utf-8"), hashlib.sha256).digest()


def hash_token(raw: str) -> str:
    """Hash opaque bearer material (refresh tokens, API keys) for at-rest storage.

    BLAKE3 keyed by nothing — these are high-entropy random tokens, not passwords.
    """
    import blake3

    return blake3.blake3(raw.encode("utf-8")).hexdigest()
