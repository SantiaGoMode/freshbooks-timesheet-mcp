import pytest

from freshbooks_mcp.config import _file_or_env


def test_value_from_env(monkeypatch):
    monkeypatch.delenv("FRESHBOOKS_CLIENT_ID_FILE", raising=False)
    monkeypatch.setenv("FRESHBOOKS_CLIENT_ID", "env-id")
    assert _file_or_env("FRESHBOOKS_CLIENT_ID") == "env-id"


def test_value_default_when_unset(monkeypatch):
    monkeypatch.delenv("FRESHBOOKS_CLIENT_ID_FILE", raising=False)
    monkeypatch.delenv("FRESHBOOKS_CLIENT_ID", raising=False)
    assert _file_or_env("FRESHBOOKS_CLIENT_ID") == ""


@pytest.mark.parametrize(
    "name", ["FRESHBOOKS_CLIENT_ID", "FRESHBOOKS_CLIENT_SECRET", "FRESHBOOKS_TOKEN_KEY"]
)
def test_file_takes_precedence_over_env(monkeypatch, tmp_path, name):
    f = tmp_path / "secret"
    f.write_text("file-value\n")  # trailing newline must be stripped
    monkeypatch.setenv(name, "env-value")
    monkeypatch.setenv(f"{name}_FILE", str(f))
    assert _file_or_env(name) == "file-value"
