"""Pluggable, secure storage for the OAuth token set.

Two backends:
- ``KeyringTokenStore`` (default): OS keychain via the ``keyring`` library.
  Encrypted at rest, OS-gated, never in the repo or backups-as-plaintext.
- ``EncryptedFileTokenStore`` (headless/Docker/CI fallback): Fernet-encrypted
  file. The key comes from config/env, never stored next to the ciphertext.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from .models import TokenSet


class TokenStore(Protocol):
    def load(self) -> TokenSet | None: ...
    def save(self, tokens: TokenSet) -> None: ...
    def clear(self) -> None: ...


class KeyringTokenStore:
    """Stores the serialized token set in the OS keychain."""

    def __init__(self, service: str, username: str) -> None:
        import keyring  # imported lazily so the file backend works without it

        self._keyring = keyring
        self._service = service
        self._username = username

    def load(self) -> TokenSet | None:
        raw = self._keyring.get_password(self._service, self._username)
        if not raw:
            return None
        return TokenSet.from_dict(json.loads(raw))

    def save(self, tokens: TokenSet) -> None:
        self._keyring.set_password(
            self._service, self._username, json.dumps(tokens.to_dict())
        )

    def clear(self) -> None:
        try:
            self._keyring.delete_password(self._service, self._username)
        except Exception:
            # delete on a missing entry raises in some backends — ignore.
            pass


class EncryptedFileTokenStore:
    """Fernet-encrypted token file with restrictive permissions + atomic write."""

    def __init__(self, path: str | Path, key: str) -> None:
        from cryptography.fernet import Fernet

        if not key:
            raise ValueError(
                "FRESHBOOKS_TOKEN_KEY is required for the file token backend. "
                "Generate one with: python -c "
                '"from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        self._path = Path(path).expanduser()
        self._fernet = Fernet(
            key.encode() if isinstance(key, str) else key
        )

    def load(self) -> TokenSet | None:
        if not self._path.exists():
            return None
        blob = self._path.read_bytes()
        data = json.loads(self._fernet.decrypt(blob))
        return TokenSet.from_dict(data)

    def save(self, tokens: TokenSet) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        blob = self._fernet.encrypt(json.dumps(tokens.to_dict()).encode())
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        # Write with 0600 from the start, then atomically replace.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self._path)
        os.chmod(self._path, 0o600)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


def default_token_path() -> Path:
    """Token path under XDG data dir (not a synced/backed-up location)."""
    base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "freshbooks-mcp" / "tokens.enc"


def build_token_store(config) -> TokenStore:
    """Construct the configured token store, falling back to keyring."""
    from .config import KEYRING_SERVICE, KEYRING_USERNAME

    if config.token_backend == "file":
        path = config.token_path or default_token_path()
        return EncryptedFileTokenStore(path, config.token_key)

    # default: keyring, with a clear error if it isn't usable
    try:
        return KeyringTokenStore(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Keyring backend unavailable. Set FRESHBOOKS_TOKEN_BACKEND=file "
            "and FRESHBOOKS_TOKEN_KEY to use the encrypted-file backend."
        ) from exc
