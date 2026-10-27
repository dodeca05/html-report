"""FastAPI dependencies shared by the routers."""

from fastapi import Request

from app.config import Settings
from app.services.file_service import ReportStorage
from app.services.token_service import TokenStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> ReportStorage:
    return request.app.state.storage


def get_tokens(request: Request) -> TokenStore:
    return request.app.state.tokens
