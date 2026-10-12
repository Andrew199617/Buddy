"""Encryption for the AI sign-ins and API keys Buddy keeps for each user.

A user can let Buddy keep a Claude plan token or an API key, which Buddy
then gives only to that user's own machines. They are stored encrypted with a
key derived from ``BUDDY_SUBSCRIPTION_SECRET_KEY``, which defaults to the key
that protects tool sign-ins and, through that, to ``WEBUI_SECRET_KEY``.

To rotate the key, set the new one and list the old ones, comma-separated, in
``BUDDY_SUBSCRIPTION_OLD_SECRET_KEYS``: values encrypted with an old key still
decrypt, and are re-encrypted with the new key the next time they are saved.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class VaultError(Exception):
    """A stored secret could not be decrypted, for example after the key changed."""


def fernet_key(secret: str) -> bytes:
    """A Fernet key for ``secret``: used as is when it already is one, else derived from it."""
    if len(secret) == 44:
        try:
            if len(base64.urlsafe_b64decode(secret.encode())) == 32:
                return secret.encode()
        except ValueError:
            pass
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


class Vault:
    def __init__(self, secret: str, old_secrets: list[str] | None = None):
        if not secret:
            raise ValueError('A secret is required to encrypt stored sign-ins.')
        keys = [Fernet(fernet_key(secret))]
        for old_secret in old_secrets or []:
            if old_secret:
                keys.append(Fernet(fernet_key(old_secret)))
        self._fernet = MultiFernet(keys)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode('utf-8')).decode('ascii')

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError, UnicodeError) as error:
            raise VaultError('A stored sign-in could not be decrypted. Sign in again.') from error

    def __repr__(self) -> str:
        return '<Vault>'


def fingerprint(secret: str) -> str:
    """A short label that tells keys apart without revealing them, like ``sk-ant-…AbCd``."""
    secret = secret.strip()
    if len(secret) <= 8:
        return '…'
    prefix = secret[:7] if secret.startswith('sk-') else ''
    return f'{prefix}…{secret[-4:]}'
