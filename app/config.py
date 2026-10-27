"""Application settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

DEFAULT_TZ = "Asia/Seoul"


def load_env_file(path: str | Path) -> None:
    """Load KEY=VALUE lines from a .env file. Variables already set in the environment win."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


class Settings(BaseModel):
    """Each field is read from the environment variable of the same name in upper case."""

    model_config = ConfigDict(frozen=True, validate_default=True)

    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    public_base_url: str | None = None
    reports_dir: Path = Path("./data/reports")
    max_upload_size_mb: float = Field(default=10, gt=0)
    upload_api_key: str | None = None
    tz: str = DEFAULT_TZ
    # Host names whose Host header may be used to build report URLs when PUBLIC_BASE_URL is unset.
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> Settings:
        if env_file is not None:
            load_env_file(env_file)
        values = {name: os.environ[name.upper()] for name in cls.model_fields if name.upper() in os.environ}
        return cls(**values)

    @field_validator("public_base_url", "upload_api_key", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("public_base_url")
    @classmethod
    def _check_public_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
            raise ValueError("PUBLIC_BASE_URL must be an absolute http:// or https:// URL, e.g. https://example.com")
        return value.rstrip("/")

    @field_validator("reports_dir")
    @classmethod
    def _resolve_reports_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("tz")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            logger.warning("Unknown time zone TZ=%r; falling back to %s", value, DEFAULT_TZ)
            return DEFAULT_TZ
        return value

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_allowed_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, (list, tuple)):
            return tuple(host.strip().strip("[]").lower() for host in value if host.strip())
        return value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in logging.getLevelNamesMapping():
            raise ValueError(f"Unknown LOG_LEVEL: {value!r}")
        return level

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_size_mb * 1024 * 1024)

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)
