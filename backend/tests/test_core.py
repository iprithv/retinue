"""Core primitives: UUIDv7, crypto envelope, passwords, JWTs."""

import time
import uuid

import jwt as pyjwt
import pytest
from cryptography.exceptions import InvalidTag

from retinue.core.crypto import SecretBox, hash_token, pepper_password
from retinue.core.ids import uuid7
from retinue.core.security import PasswordService, TokenService


class TestUuid7:
    def test_version_and_variant(self):
        value = uuid7()
        assert value.version == 7
        assert value.variant == uuid.RFC_4122

    def test_time_ordered(self):
        ids = [uuid7() for _ in range(2000)]
        assert ids == sorted(ids), "uuid7 must sort in generation order"

    def test_timestamp_embedded(self):
        before = time.time_ns() // 1_000_000
        value = uuid7()
        after = time.time_ns() // 1_000_000
        embedded = value.int >> 80
        assert before <= embedded <= after + 1


class TestSecretBox:
    def test_roundtrip(self):
        box = SecretBox("master-secret")
        ciphertext, nonce = box.encrypt(b"sk-super-secret")
        assert ciphertext != b"sk-super-secret"
        assert box.decrypt(ciphertext, nonce) == b"sk-super-secret"

    def test_nonces_unique(self):
        box = SecretBox("master-secret")
        nonces = {box.encrypt(b"x")[1] for _ in range(100)}
        assert len(nonces) == 100

    def test_wrong_key_fails(self):
        ciphertext, nonce = SecretBox("secret-a").encrypt(b"payload")
        with pytest.raises(InvalidTag):
            SecretBox("secret-b").decrypt(ciphertext, nonce)

    def test_purpose_scoping(self):
        ciphertext, nonce = SecretBox("s", purpose="credentials").encrypt(b"payload")
        with pytest.raises(InvalidTag):
            SecretBox("s", purpose="other").decrypt(ciphertext, nonce)


class TestPasswords:
    def test_hash_verify(self):
        service = PasswordService("master")
        stored = service.hash("correct horse battery staple")
        assert service.verify(stored, "correct horse battery staple")
        assert not service.verify(stored, "wrong password")

    def test_pepper_binds_to_secret(self):
        stored = PasswordService("master-a").hash("password123")
        assert not PasswordService("master-b").verify(stored, "password123")

    def test_pepper_deterministic(self):
        assert pepper_password("s", "p") == pepper_password("s", "p")
        assert pepper_password("s", "p") != pepper_password("s", "q")


class TestTokens:
    def test_jwt_roundtrip(self, tmp_path):
        service = TokenService(tmp_path / "key.pem", access_ttl_s=900)
        user_id = uuid7()
        token = service.make_access_token(user_id=user_id, role="member", session_version=3)
        claims = service.decode_access_token(token)
        assert claims["sub"] == str(user_id)
        assert claims["role"] == "member"
        assert claims["sv"] == 3
        assert claims["iss"] == "retinue"

    def test_key_persists(self, tmp_path):
        path = tmp_path / "key.pem"
        token = TokenService(path, 900).make_access_token(
            user_id=uuid7(), role="member", session_version=1
        )
        # a new service instance loads the same key and can verify
        TokenService(path, 900).decode_access_token(token)

    def test_expired_rejected(self, tmp_path):
        service = TokenService(tmp_path / "key.pem", access_ttl_s=-10)
        token = service.make_access_token(user_id=uuid7(), role="member", session_version=1)
        with pytest.raises(pyjwt.ExpiredSignatureError):
            service.decode_access_token(token)

    def test_tampered_rejected(self, tmp_path):
        service = TokenService(tmp_path / "key.pem", access_ttl_s=900)
        token = service.make_access_token(user_id=uuid7(), role="member", session_version=1)
        with pytest.raises(pyjwt.InvalidTokenError):
            service.decode_access_token(token[:-4] + "AAAA")


def test_hash_token_stable():
    assert hash_token("rtr_abc") == hash_token("rtr_abc")
    assert hash_token("rtr_abc") != hash_token("rtr_abd")
