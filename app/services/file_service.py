"""Filesystem storage for HTML reports: name validation, atomic saves and directory scans."""

from __future__ import annotations

import codecs
import logging
import os
import tempfile
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

HTML_SUFFIX = ".html"
# Keeps "<name>.html" well below the 255-byte file name limit of common filesystems.
MAX_NAME_BYTES = 200
# Path separators plus characters that are reserved on Windows filesystems.
_FORBIDDEN_CHARS = frozenset('/\\:*?"<>|')
# Control, format (e.g. bidi overrides), surrogate, private-use and unassigned code points, line/paragraph separators.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
_TEMP_PREFIX = ".upload-"
_TEMP_SUFFIX = ".tmp"
_CHUNK_SIZE = 64 * 1024
_STALE_TEMP_SECONDS = 60 * 60


class ReportError(Exception):
    """An upload was rejected because of the client's input."""


class InvalidReportNameError(ReportError):
    pass


class UnsupportedFileTypeError(ReportError):
    pass


class InvalidReportContentError(ReportError):
    pass


class ReportTooLargeError(ReportError):
    pass


class ReportAlreadyExistsError(ReportError):
    pass


class ReportNotFoundError(ReportError):
    pass


@dataclass(frozen=True)
class ReportInfo:
    name: str
    """Report name without the extension, as used in /docs/{name}."""
    filename: str
    size: int
    uploaded_at: datetime


def normalize_report_name(raw: str) -> str:
    """Validate a report name (without extension) and return its canonical NFC form.

    NFC matters for Korean names: macOS clients may send decomposed (NFD) Hangul, while
    browsers request the composed form.
    """
    name = unicodedata.normalize("NFC", raw)
    if not name:
        raise InvalidReportNameError("Report name must not be empty.")
    if name != name.strip():
        raise InvalidReportNameError("Report name must not start or end with whitespace.")
    if ".." in name:
        raise InvalidReportNameError("Report name must not contain '..'.")
    if name.startswith(".") or name.endswith("."):
        raise InvalidReportNameError("Report name must not start or end with '.'.")
    for char in name:
        if char in _FORBIDDEN_CHARS or unicodedata.category(char) in _FORBIDDEN_CATEGORIES:
            raise InvalidReportNameError(f"Report name contains a forbidden character: {char!r}.")
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise InvalidReportNameError(f"Report name must be at most {MAX_NAME_BYTES} bytes in UTF-8.")
    return name


def check_html_filename(original_filename: str | None) -> str:
    """Reject uploaded files that are not .html; returns the stripped file name."""
    original = (original_filename or "").strip()
    if original[-len(HTML_SUFFIX) :].lower() != HTML_SUFFIX:
        raise UnsupportedFileTypeError("Only .html files can be uploaded.")
    return original


def resolve_upload_name(original_filename: str | None, requested_name: str | None) -> str:
    """Pick the report name for an upload: the `name` field if given, otherwise the uploaded file's name."""
    original = check_html_filename(original_filename)

    if requested_name is not None and requested_name.strip():
        candidate = requested_name.strip()
        if candidate[-len(HTML_SUFFIX) :].lower() == HTML_SUFFIX:
            candidate = candidate[: -len(HTML_SUFFIX)]
    else:
        candidate = original[: -len(HTML_SUFFIX)]
    return normalize_report_name(candidate)


class ReportStorage:
    """Stores reports as `<name>.html` files directly inside one directory. No database involved."""

    def __init__(self, reports_dir: Path, max_bytes: int, tz: ZoneInfo) -> None:
        self.reports_dir = reports_dir
        self.max_bytes = max_bytes
        self.tz = tz

    def ensure_directory(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def is_writable(self) -> bool:
        return os.access(self.reports_dir, os.W_OK)

    def remove_stale_temp_files(self) -> int:
        """Delete temp files left behind by uploads that were interrupted by a crash."""
        cutoff = time.time() - _STALE_TEMP_SECONDS
        removed = 0
        for path in self.reports_dir.glob(f"{_TEMP_PREFIX}*{_TEMP_SUFFIX}"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        return removed

    def find(self, name: str) -> Path | None:
        """Return the file for /docs/{name}, or None if there is no such report."""
        try:
            stem = normalize_report_name(name)
        except InvalidReportNameError:
            return None
        path = self.reports_dir / f"{stem}{HTML_SUFFIX}"
        # Only regular files directly inside the reports directory are served; symlinks are never followed.
        if path.is_symlink() or not path.is_file():
            return None
        return path

    def list_reports(self) -> list[ReportInfo]:
        """Scan the reports directory, newest first."""
        try:
            entries = list(os.scandir(self.reports_dir))
        except FileNotFoundError:
            return []

        reports = []
        for entry in entries:
            if not entry.name.endswith(HTML_SUFFIX) or not entry.is_file(follow_symlinks=False):
                continue
            name = entry.name[: -len(HTML_SUFFIX)]
            if not _is_servable_name(name):
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            reports.append(
                ReportInfo(name=name, filename=entry.name, size=stat.st_size, uploaded_at=self._to_local(stat.st_mtime))
            )
        reports.sort(key=lambda report: (report.uploaded_at, report.name), reverse=True)
        return reports

    def save(self, name: str, source: BinaryIO) -> ReportInfo:
        """Store `source` as `<name>.html` without ever overwriting an existing report.

        The content is written to a temp file in the same directory and only then published
        under its final name, so a failed upload never leaves a partial report behind.
        """
        stem = normalize_report_name(name)
        target = self.reports_dir / f"{stem}{HTML_SUFFIX}"
        self.ensure_directory()
        if os.path.lexists(target):
            raise ReportAlreadyExistsError(f"Report '{target.name}' already exists.")
        return self._store(target, source, self._publish)

    def replace(self, name: str, source: BinaryIO) -> ReportInfo:
        """Overwrite an existing report. Readers see either the old or the new file, never a partial one."""
        target = self.find(name)
        if target is None:
            raise ReportNotFoundError("Report not found.")
        return self._store(target, source, os.replace)

    def delete(self, name: str) -> str:
        """Delete a report and return its file name."""
        target = self.find(name)
        if target is None:
            raise ReportNotFoundError("Report not found.")
        try:
            target.unlink()
        except FileNotFoundError:
            raise ReportNotFoundError("Report not found.") from None
        return target.name

    def _store(self, target: Path, source: BinaryIO, publish: Callable[[Path, Path], None]) -> ReportInfo:
        """Write `source` to a temp file next to `target`, then move it into place with `publish`."""
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                size = self._copy_validated(source, out)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temp_path, 0o644)
            publish(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)

        stat = target.stat()
        return ReportInfo(
            name=target.name[: -len(HTML_SUFFIX)],
            filename=target.name,
            size=size,
            uploaded_at=self._to_local(stat.st_mtime),
        )

    def _copy_validated(self, source: BinaryIO, out: BinaryIO) -> int:
        decoder = codecs.getincrementaldecoder("utf-8")()
        size = 0
        while chunk := source.read(_CHUNK_SIZE):
            size += len(chunk)
            if size > self.max_bytes:
                raise ReportTooLargeError(f"File is too large. Maximum upload size is {_format_mb(self.max_bytes)} MB.")
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError:
                raise InvalidReportContentError("HTML file must be UTF-8 encoded.") from None
            out.write(chunk)
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            raise InvalidReportContentError("HTML file must be UTF-8 encoded.") from None
        if size == 0:
            raise InvalidReportContentError("HTML file is empty.")
        return size

    @staticmethod
    def _publish(temp_path: Path, target: Path) -> None:
        """Give the temp file its final name atomically, failing if the name is already taken."""
        try:
            # link() never replaces an existing file, so concurrent uploads of one name cannot clobber each other.
            os.link(temp_path, target)
            return
        except FileExistsError:
            raise ReportAlreadyExistsError(f"Report '{target.name}' already exists.") from None
        except OSError:
            logger.debug("Hard links unsupported in %s; using exclusive create + rename", target.parent)

        # Fallback for filesystems without hard links: reserve the name, then atomically replace the placeholder.
        try:
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        except FileExistsError:
            raise ReportAlreadyExistsError(f"Report '{target.name}' already exists.") from None
        try:
            os.replace(temp_path, target)
        except OSError:
            target.unlink(missing_ok=True)
            raise

    def _to_local(self, timestamp: float) -> datetime:
        return datetime.fromtimestamp(timestamp, self.tz).replace(microsecond=0)


def _is_servable_name(name: str) -> bool:
    try:
        return normalize_report_name(name) == name
    except InvalidReportNameError:
        return False


def _format_mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):g}"
