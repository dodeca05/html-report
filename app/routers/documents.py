"""Public pages: the report list (/index) and the reports themselves (/docs/{name})."""

import logging
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_storage
from app.schemas import ErrorResponse
from app.services.file_service import ReportStorage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

# Uploaded HTML runs as an isolated, opaque-origin document: scripts work (charts, interactivity), but
# without allow-same-origin it cannot read this site's pages, cookies or storage, nor register service workers.
REPORT_CSP = "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads"
INDEX_CSP = (
    "default-src 'none'; style-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)


@router.get("/", include_in_schema=False)
def root(request: Request) -> RedirectResponse:
    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/index")


@router.get("/docs", include_in_schema=False)
def docs_root(request: Request) -> RedirectResponse:
    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/index")


@router.get("/index", response_class=HTMLResponse, summary="업로드된 보고서 목록")
def report_index(request: Request, storage: Annotated[ReportStorage, Depends(get_storage)]) -> HTMLResponse:
    reports = [
        {
            "name": report.name,
            # Relative link so the page also works when served under a path prefix.
            "href": f"docs/{quote(report.name, safe='')}",
            "uploaded_at": report.uploaded_at.strftime("%Y-%m-%d %H:%M:%S"),
            "uploaded_at_iso": report.uploaded_at.isoformat(),
        }
        for report in storage.list_reports()
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {"reports": reports, "root_path": request.scope.get("root_path", "")},
        headers={"Content-Security-Policy": INDEX_CSP, "X-Content-Type-Options": "nosniff"},
    )


@router.get(
    "/docs/{name}",
    response_class=FileResponse,
    summary="HTML 보고서 조회",
    description="`{name}.html` 파일을 원본 그대로 반환합니다. 보고서는 CSP sandbox로 격리되어 실행됩니다.",
    responses={
        status.HTTP_200_OK: {"content": {"text/html": {}}, "description": "업로드된 원본 HTML"},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "보고서 없음"},
    },
)
def view_report(name: str, storage: Annotated[ReportStorage, Depends(get_storage)]) -> FileResponse:
    path = storage.find(name)
    if path is None:
        logger.debug("Report not found: %r", name)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": REPORT_CSP,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
        },
    )
