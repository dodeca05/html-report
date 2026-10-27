"""API token storage: tokens.csv (name, token, expires_at) and settings.json (token authentication on/off)."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import secrets
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "hrs_"
MAX_TOKEN_NAME_LENGTH = 50
CSV_FIELDS = ("name", "token", "expires_at")
# Expiry choices offered on the settings page (key -> label). "Forever" is 999 years.
EXPIRY_CHOICES: dict[str, str] = {"7d": "7일", "30d": "30일", "1y": "1년", "forever": "영구 (999년)"}
_EXPIRY_DAYS = {"7d": 7, "30d": 30}
_EXPIRY_YEARS = {"1y": 1, "forever": 999}
# Spreadsheet apps evaluate cells starting with these characters as formulas when the CSV is opened.
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


class TokenError(Exception):
    """A token change was rejected because of the user's input. Messages are shown on the settings page."""


class InvalidTokenNameError(TokenError):
    pass


class DuplicateTokenNameError(TokenError):
    pass


class InvalidExpiryError(TokenError):
    pass


class TokenNotFoundError(TokenError):
    pass


@dataclass(frozen=True)
class ApiToken:
    name: str
    token: str
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    @property
    def masked(self) -> str:
        return f"{self.token[:8]}…{self.token[-4:]}"


def normalize_token_name(raw: str) -> str:
    name = unicodedata.normalize("NFC", raw).strip()
    if not name:
        raise InvalidTokenNameError("토큰 이름을 입력하세요.")
    if len(name) > MAX_TOKEN_NAME_LENGTH:
        raise InvalidTokenNameError(f"토큰 이름은 {MAX_TOKEN_NAME_LENGTH}자 이하로 입력하세요.")
    if any(unicodedata.category(char) in _FORBIDDEN_CATEGORIES for char in name):
        raise InvalidTokenNameError("토큰 이름에 제어 문자는 사용할 수 없습니다.")
    if name.startswith(_FORMULA_PREFIXES):
        raise InvalidTokenNameError("토큰 이름은 =, +, -, @ 로 시작할 수 없습니다.")
    return name


def compute_expiry(choice: str, now: datetime) -> datetime:
    now = now.replace(microsecond=0)
    if choice in _EXPIRY_DAYS:
        return now + timedelta(days=_EXPIRY_DAYS[choice])
    if choice in _EXPIRY_YEARS:
        year = now.year + _EXPIRY_YEARS[choice]
        try:
            return now.replace(year=year)
        except ValueError:  # 29 February in a non-leap target year
            return now.replace(year=year, day=28)
    raise InvalidExpiryError("만료 기간을 선택하세요.")


class TokenStore:
    """Keeps tokens in a CSV file and the on/off switch in a JSON file, both inside one config directory.

    Files are re-read on every call, so manual edits take effect immediately, and rewritten atomically
    with owner-only permissions because they hold secrets.
    """

    def __init__(self, config_dir: Path, tz: ZoneInfo) -> None:
        self.config_dir = config_dir
        self.tz = tz
        self.tokens_path = config_dir / "tokens.csv"
        self.settings_path = config_dir / "settings.json"
        self._lock = threading.Lock()

    def ensure_directory(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def is_writable(self) -> bool:
        return os.access(self.config_dir, os.W_OK)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def auth_enabled(self) -> bool:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return True
        except (OSError, ValueError):
            logger.error("Cannot read %s; keeping token authentication on", self.settings_path)
            return True
        # Fail closed: only an explicit false turns authentication off.
        return not (isinstance(data, dict) and data.get("token_auth_enabled") is False)

    def set_auth_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._write_atomic(self.settings_path, json.dumps({"token_auth_enabled": enabled}, indent=2) + "\n")

    def list_tokens(self) -> list[ApiToken]:
        try:
            with self.tokens_path.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
        except FileNotFoundError:
            return []

        tokens = []
        for line_number, row in enumerate(rows, start=2):
            try:
                token = row["token"].strip()
                if not token:
                    raise ValueError("empty token")
                tokens.append(
                    ApiToken(name=row["name"].strip(), token=token, expires_at=self._parse_expiry(row["expires_at"]))
                )
            except (KeyError, AttributeError, ValueError):
                logger.warning("Skipping malformed line %d in %s", line_number, self.tokens_path)
        return tokens

    def has_tokens(self) -> bool:
        return bool(self.list_tokens())

    def lookup(self, value: str) -> ApiToken | None:
        """Find the token with this value (expired or not). Compares against every token in constant time."""
        match = None
        for token in self.list_tokens():
            if secrets.compare_digest(token.token.encode(), value.encode()):
                match = token
        return match

    def create(self, name: str, expiry: str) -> ApiToken:
        name = normalize_token_name(name)
        if expiry not in EXPIRY_CHOICES:
            raise InvalidExpiryError("만료 기간을 선택하세요.")
        with self._lock:
            tokens = self.list_tokens()
            if any(token.name == name for token in tokens):
                raise DuplicateTokenNameError(f"같은 이름의 토큰이 이미 있습니다: {name}")
            token = ApiToken(
                name=name,
                token=TOKEN_PREFIX + secrets.token_urlsafe(32),
                expires_at=compute_expiry(expiry, self.now()),
            )
            self._save_tokens([*tokens, token])
        return token

    def delete(self, name: str) -> None:
        with self._lock:
            tokens = self.list_tokens()
            remaining = [token for token in tokens if token.name != name]
            if len(remaining) == len(tokens):
                raise TokenNotFoundError("토큰을 찾을 수 없습니다.")
            self._save_tokens(remaining)

    def _parse_expiry(self, value: str) -> datetime:
        expires_at = datetime.fromisoformat(value.strip())
        # Dates typed by hand without an offset are read in the configured time zone.
        return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=self.tz)

    def _save_tokens(self, tokens: list[ApiToken]) -> None:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_FIELDS)
        for token in tokens:
            writer.writerow([token.name, token.token, token.expires_at.isoformat()])
        # UTF-8 with BOM so spreadsheet apps show Korean names correctly.
        self._write_atomic(self.tokens_path, buffer.getvalue(), encoding="utf-8-sig")

    def _write_atomic(self, path: Path, text: str, encoding: str = "utf-8") -> None:
        self.ensure_directory()
        # mkstemp creates the file with 0600 permissions, which os.replace keeps.
        fd, temp_name = tempfile.mkstemp(dir=self.config_dir, prefix=f".{path.name}-", suffix=".tmp")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline="") as file:
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
