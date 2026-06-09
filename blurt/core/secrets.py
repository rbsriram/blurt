"""Encrypt jotted secrets at rest, with the key held in the OS keychain.

The threat model is deliberately modest (see docs/DECISIONS.md): this makes a
jotted credential meaningfully safer than a plaintext note (encrypted on disk,
never written to the scratchpad.md mirror, never embedded into the search index),
but it is NOT a password manager. Once the app has decrypted a value into memory,
anything running as you can read it.

Design:
- A single Fernet key (AES-128-CBC + HMAC, the hard-to-misuse high-level recipe)
  lives in the OS keychain via `keyring` (macOS Keychain / Windows Credential
  Locker / Linux Secret Service). No master password, so it stays frictionless.
- Secret VALUES are encrypted and stored in their own table; the note's content is
  only the human label ("gmail password"). So the value never touches the mirror
  or the embedding index, and the "content is verbatim" invariant is untouched.
- The key is per-machine: copy the DB to another machine and secrets won't decrypt
  there (by design). That's the price of not having a portable master password.
"""

from __future__ import annotations

import keyring
from cryptography.fernet import Fernet

_SERVICE = "blurt"
_ACCOUNT = "secret-encryption-key"


def _load_or_create_key() -> bytes:
    """Fetch the Fernet key from the OS keychain, creating it on first use.

    Raises whatever the keyring backend raises if no secure store is available
    (e.g. a headless Linux box with no Secret Service); the caller turns that into
    "secret storage unavailable" rather than crashing the app.
    """
    existing = keyring.get_password(_SERVICE, _ACCOUNT)
    if existing:
        return existing.encode()
    key = Fernet.generate_key()
    keyring.set_password(_SERVICE, _ACCOUNT, key.decode())
    return key


class SecretVault:
    """Encrypts/decrypts secret strings with a fixed key. Construct via open()."""

    def __init__(self, key: bytes):
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()


def open_vault() -> SecretVault | None:
    """Open the vault backed by the OS keychain, or None if no keychain is available.

    None means the secrets feature degrades to disabled rather than erroring: the
    rest of Blurt is unaffected, and the UI can hide the affordance.
    """
    try:
        return SecretVault(_load_or_create_key())
    except Exception:
        return None
