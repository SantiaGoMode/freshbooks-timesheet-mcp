import os
import stat

import pytest
from cryptography.fernet import Fernet

from freshbooks_mcp.models import TokenSet
from freshbooks_mcp.token_store import EncryptedFileTokenStore


def test_encrypted_file_roundtrip(tmp_path):
    path = tmp_path / "tokens.enc"
    key = Fernet.generate_key().decode()
    store = EncryptedFileTokenStore(path, key)

    assert store.load() is None  # nothing yet

    ts = TokenSet("acc", "ref", 123.0)
    store.save(ts)
    assert store.load() == ts


def test_encrypted_file_is_not_plaintext(tmp_path):
    path = tmp_path / "tokens.enc"
    store = EncryptedFileTokenStore(path, Fernet.generate_key().decode())
    store.save(TokenSet("plaintextsecret", "ref", 1.0))
    blob = path.read_bytes()
    assert b"plaintextsecret" not in blob


def test_encrypted_file_permissions(tmp_path):
    path = tmp_path / "tokens.enc"
    store = EncryptedFileTokenStore(path, Fernet.generate_key().decode())
    store.save(TokenSet("a", "b", 1.0))
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_encrypted_file_clear(tmp_path):
    path = tmp_path / "tokens.enc"
    store = EncryptedFileTokenStore(path, Fernet.generate_key().decode())
    store.save(TokenSet("a", "b", 1.0))
    store.clear()
    assert store.load() is None


def test_encrypted_file_requires_key(tmp_path):
    with pytest.raises(ValueError):
        EncryptedFileTokenStore(tmp_path / "x.enc", "")


def test_wrong_key_cannot_decrypt(tmp_path):
    path = tmp_path / "tokens.enc"
    EncryptedFileTokenStore(path, Fernet.generate_key().decode()).save(
        TokenSet("a", "b", 1.0)
    )
    other = EncryptedFileTokenStore(path, Fernet.generate_key().decode())
    with pytest.raises(Exception):
        other.load()
