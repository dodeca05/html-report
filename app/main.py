"""FastAPI application factory and ASGI entry point (`uvicorn app.main:app`)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Settings
from app.middleware import BodySizeLimitMiddleware
from app.routers import documents, settings_page, upload
from app.services.file_service import ReportStorage
from app.services.token_service import TokenStore

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Room for multipart boundaries, part headers and the `name` field on top of the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT)
    logging.getLogger("app").setLevel(level)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    storage = ReportStorage(settings.reports_dir, settings.max_upload_bytes, settings.timezone)
    tokens = TokenStore(settings.config_dir, settings.timezone)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        storage.ensure_directory()
        tokens.ensure_directory()
        removed = storage.remove_stale_temp_files()
        if removed:
            logger.info("Removed %d stale temp file(s) from interrupted uploads", removed)
        _log_startup(settings, storage, tokens)
        yield

    app = FastAPI(
        title="HTML Report Server",
        version=__version__,
        description="AI 코딩 에이전트가 생성한 HTML 보고서를 업로드하고 브라우저에서 조회하는 서비스",
        # FastAPI's default /docs would collide with the report URLs.
        docs_url="/api/swagger",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.storage = storage
    app.state.tokens = tokens
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_size=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
        detail=f"File is too large. Maximum upload size is {settings.max_upload_size_mb:g} MB.",
    )
    app.include_router(upload.router)
    app.include_router(documents.router)
    app.include_router(settings_page.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _log_startup(settings: Settings, storage: ReportStorage, tokens: TokenStore) -> None:
    logger.info("Reports directory: %s", settings.reports_dir)
    if not storage.is_writable():
        logger.error("Reports directory %s is not writable; uploads will fail", settings.reports_dir)
    logger.info("Config directory: %s", settings.config_dir)
    if not tokens.is_writable():
        logger.error("Config directory %s is not writable; token changes will fail", settings.config_dir)
    if settings.upload_api_key is None:
        logger.warning("UPLOAD_API_KEY (admin key) is not set: the settings page is disabled")
    elif settings.upload_api_key == "change-me":
        logger.warning("UPLOAD_API_KEY is still the example value 'change-me'; set a secret key")
    if tokens.auth_enabled():
        logger.info("Token authentication is ON (%d token(s) in %s)", len(tokens.list_tokens()), tokens.tokens_path)
    else:
        logger.warning("Token authentication is OFF: anyone can upload, replace and delete reports")
    if settings.public_base_url:
        logger.info("Report URLs use PUBLIC_BASE_URL=%s", settings.public_base_url)
    else:
        logger.warning(
            "PUBLIC_BASE_URL is not set: report URLs use the Host header only for ALLOWED_HOSTS=%s",
            ",".join(settings.allowed_hosts),
        )


app = create_app()

