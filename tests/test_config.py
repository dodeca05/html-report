from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, load_env_file

ENV_VARS = [name.upper() for name in Settings.model_fields]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    settings = Settings.from_env(env_file=None)

    assert settings.port == 8000
    assert settings.public_base_url is None
    assert settings.upload_api_key is None
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.reports_dir == Path("data/reports").resolve()
    assert settings.config_dir == Path("data/config").resolve()
    assert settings.tz == "Asia/Seoul"


def test_reads_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com/")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "r"))
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "2.5")
    monkeypatch.setenv("UPLOAD_API_KEY", " secret ")
    monkeypatch.setenv("ALLOWED_HOSTS", "Reports.Internal, [::1]")

    settings = Settings.from_env(env_file=None)

    assert settings.port == 9000
    assert settings.public_base_url == "https://example.com"
    assert settings.reports_dir == tmp_path / "r"
    assert settings.max_upload_bytes == int(2.5 * 1024 * 1024)
    assert settings.upload_api_key == "secret"
    assert settings.allowed_hosts == ("reports.internal", "::1")


def test_blank_api_key_disables_upload(monkeypatch):
    monkeypatch.setenv("UPLOAD_API_KEY", "   ")

    assert Settings.from_env(env_file=None).upload_api_key is None


@pytest.mark.parametrize("url", ["example.com", "ftp://example.com", "https://", "https://example.com/?a=1"])
def test_rejects_invalid_public_base_url(url):
    with pytest.raises(ValidationError):
        Settings(public_base_url=url)


def test_rejects_non_positive_upload_size():
    with pytest.raises(ValidationError):
        Settings(max_upload_size_mb=0)


def test_unknown_timezone_falls_back_to_seoul():
    assert Settings(tz="Mars/Olympus").tz == "Asia/Seoul"


def test_env_file_does_not_override_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "PORT=9001\n"
        "export PUBLIC_BASE_URL='https://from-file.example'\n"
        'UPLOAD_API_KEY="file-key"\n'
        "MALFORMED LINE\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UPLOAD_API_KEY", "env-key")

    load_env_file(env_file)
    settings = Settings.from_env(env_file=None)

    assert settings.port == 9001
    assert settings.public_base_url == "https://from-file.example"
    assert settings.upload_api_key == "env-key"
