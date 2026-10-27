"""JSON API under /api: public report list (GET) and API-key protected upload (POST), replace (PUT), delete (DELETE)."""

import logging
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, Security, UploadFile, status
from fastapi.security import APIKeyHeader

from app.config import Settings
from app.dependencies import get_settings, get_storage
from app.schemas import ErrorResponse, ReportItem, ReportListResponse, UploadResponse
from app.services.file_service import (
    InvalidReportContentError,
    InvalidReportNameError,
    ReportAlreadyExistsError,
    ReportError,
    ReportInfo,
    ReportNotFoundError,
    ReportStorage,
    ReportTooLargeError,
    UnsupportedFileTypeError,
    check_html_filename,
    resolve_upload_name,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="UPLOAD_API_KEY 환경변수에 설정한 업로드 키",
)

_STATUS_BY_ERROR: dict[type[ReportError], int] = {
    InvalidReportNameError: status.HTTP_400_BAD_REQUEST,
    InvalidReportContentError: status.HTTP_400_BAD_REQUEST,
    UnsupportedFileTypeError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    ReportTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    ReportAlreadyExistsError: status.HTTP_409_CONFLICT,
    ReportNotFoundError: status.HTTP_404_NOT_FOUND,
}


def _error(description: str) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description}


_AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: _error("API 키 누락 또는 불일치"),
    status.HTTP_503_SERVICE_UNAVAILABLE: _error("UPLOAD_API_KEY 미설정으로 API 비활성화"),
}
_CONTENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: _error("잘못된 파일 이름 또는 내용 (빈 파일, UTF-8 아님)"),
    status.HTTP_413_CONTENT_TOO_LARGE: _error("최대 업로드 크기 초과"),
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: _error(".html 이외의 파일"),
}

# host[:port] where host is a DNS name, an IPv4 address or a bracketed IPv6 address.
_HOST_HEADER_RE = re.compile(r"(?P<host>\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::\d{1,5})?")


def require_api_key(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> None:
    if settings.upload_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upload API is disabled. Set UPLOAD_API_KEY on the server to enable it.",
        )
    if api_key is None or not secrets.compare_digest(api_key.encode(), settings.upload_api_key.encode()):
        logger.warning(
            "Rejected %s %s from %s: %s API key",
            request.method,
            request.url.path,
            _client_host(request),
            "missing" if api_key is None else "invalid",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "APIKey"},
        )


@router.get(
    "/docs",
    response_model=ReportListResponse,
    tags=["documents"],
    summary="보고서 목록 조회 (JSON)",
    description="업로드된 보고서를 최신순으로 반환합니다. /index 페이지와 같은 내용이며 인증 없이 공개됩니다.",
)
def list_reports(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[ReportStorage, Depends(get_storage)],
) -> ReportListResponse:
    base_url = _public_base_url(request, settings)
    reports = [
        ReportItem(
            name=report.name,
            filename=report.filename,
            url=_report_url(base_url, report.name),
            size=report.size,
            uploaded_at=report.uploaded_at,
        )
        for report in storage.list_reports()
    ]
    return ReportListResponse(count=len(reports), reports=reports)


@router.post(
    "/docs",
    status_code=status.HTTP_201_CREATED,
    tags=["upload"],
    response_model=UploadResponse,
    summary="HTML 보고서 업로드",
    description=(
        "multipart/form-data의 `file` 필드로 .html 파일을 업로드합니다. "
        "`name` 필드를 주면 그 이름으로 저장하며, 없으면 원본 파일 이름을 사용합니다. "
        "같은 이름이 이미 있으면 409를 반환하고 기존 파일을 덮어쓰지 않습니다(교체는 PUT 사용)."
    ),
    dependencies=[Depends(require_api_key)],
    responses={
        **_AUTH_RESPONSES,
        **_CONTENT_RESPONSES,
        status.HTTP_409_CONFLICT: _error("같은 이름의 보고서가 이미 존재"),
    },
)
def upload_report(
    request: Request,
    file: Annotated[UploadFile, File(description="업로드할 .html 파일")],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[ReportStorage, Depends(get_storage)],
    name: Annotated[str | None, Form(description="저장할 보고서 이름 (선택, .html 생략 가능)")] = None,
) -> UploadResponse:
    with _storage_errors(request, "upload", file.filename if name is None else name):
        report = storage.save(resolve_upload_name(file.filename, name), file.file)
    logger.info("Stored report %s (%d bytes) from %s", report.filename, report.size, _client_host(request))
    return _upload_response(request, settings, report)


@router.put(
    "/docs/{name}",
    response_model=UploadResponse,
    tags=["upload"],
    summary="HTML 보고서 교체",
    description=(
        "기존 보고서 `{name}.html` 의 내용을 새 .html 파일로 교체합니다. 보고서 주소는 그대로이고 "
        "업로드 날짜는 교체 시각으로 바뀝니다. 보고서가 없으면 404를 반환합니다(새로 만들 때는 POST 사용)."
    ),
    dependencies=[Depends(require_api_key)],
    responses={
        **_AUTH_RESPONSES,
        **_CONTENT_RESPONSES,
        status.HTTP_404_NOT_FOUND: _error("교체할 보고서 없음"),
    },
)
def replace_report(
    request: Request,
    name: str,
    file: Annotated[UploadFile, File(description="새 내용이 담긴 .html 파일")],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[ReportStorage, Depends(get_storage)],
) -> UploadResponse:
    with _storage_errors(request, "replace", name):
        check_html_filename(file.filename)
        report = storage.replace(name, file.file)
    logger.info("Replaced report %s (%d bytes) from %s", report.filename, report.size, _client_host(request))
    return _upload_response(request, settings, report)


@router.delete(
    "/docs/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["upload"],
    summary="HTML 보고서 삭제",
    description="보고서 `{name}.html` 을 삭제합니다. 성공하면 본문 없이 204를 반환합니다.",
    dependencies=[Depends(require_api_key)],
    responses={**_AUTH_RESPONSES, status.HTTP_404_NOT_FOUND: _error("삭제할 보고서 없음")},
)
def delete_report(
    request: Request,
    name: str,
    storage: Annotated[ReportStorage, Depends(get_storage)],
) -> None:
    with _storage_errors(request, "delete", name):
        filename = storage.delete(name)
    logger.info("Deleted report %s from %s", filename, _client_host(request))


@contextmanager
def _storage_errors(request: Request, action: str, target: str | None) -> Iterator[None]:
    """Translate storage errors into HTTP errors: client mistakes to 4xx, filesystem failures to 500."""
    try:
        yield
    except ReportError as exc:
        logger.warning("Rejected %s of %r from %s: %s", action, target, _client_host(request), exc)
        raise HTTPException(status_code=_STATUS_BY_ERROR[type(exc)], detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("Failed to %s %r", action, target)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to {action} the report."
        ) from exc


def _upload_response(request: Request, settings: Settings, report: ReportInfo) -> UploadResponse:
    url = _report_url(_public_base_url(request, settings), report.name)
    return UploadResponse(filename=report.filename, url=url, uploaded_at=report.uploaded_at)


def _report_url(base_url: str, name: str) -> str:
    return f"{base_url}/docs/{quote(name, safe='')}"


def _public_base_url(request: Request, settings: Settings) -> str:
    """Base URL for links handed back to clients.

    PUBLIC_BASE_URL always wins. Without it the request's Host header is used only when it names
    an allowed host, so a forged Host header cannot make the API return links to another site.
    """
    if settings.public_base_url:
        return settings.public_base_url

    host_header = request.headers.get("host", "")
    match = _HOST_HEADER_RE.fullmatch(host_header)
    if match:
        hostname = match["host"].strip("[]").lower()
        if "*" in settings.allowed_hosts or hostname in settings.allowed_hosts:
            scheme = request.scope.get("scheme", "http")
            return f"{scheme}://{host_header}{request.scope.get('root_path', '')}"

    fallback = f"http://localhost:{settings.port}"
    logger.warning(
        "Host header %r is not in ALLOWED_HOSTS; returning a %s URL. Set PUBLIC_BASE_URL to fix report URLs.",
        host_header,
        fallback,
    )
    return fallback


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"
