"""Encrypts the Gmail refresh token at rest (Fernet: AES-128-CBC + HMAC-SHA256, authenticated).

The key comes from the environment and is never stored in the database. `key_id` (a short hash of the key) is stored
next to the ciphertext so a key change is detected and treated as "consent must be given again", not as a crash.
"""
from __future__ import annotations

import hashlib

from cryptography.fernet import Fernet, InvalidToken


class VaultError(Exception):
    pass


class TokenVault:
    def __init__(self, key: str):
        if not key:
            raise VaultError("EMAIL_TOKEN_ENCRYPTION_KEY is not configured")
        self._fernet = Fernet(key.encode())
        self.key_id = hashlib.sha256(key.encode()).hexdigest()[:12]

    def encrypt(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def decrypt(self, blob: bytes, key_id: str | None) -> str:
        if key_id != self.key_id:
            raise VaultError("the stored token was encrypted with a different key")
        try:
            return self._fernet.decrypt(bytes(blob)).decode()
        except InvalidToken:
            raise VaultError("the stored token could not be decrypted") from None
