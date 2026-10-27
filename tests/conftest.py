from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

API_KEY = "test-api-key"
BASE_URL = "https://reports.example.com"
SAMPLE_HTML = """<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>성능 보고서</title>
<style>h1 { color: #2f5bd3; }</style></head>
<body><h1>성능 보고서</h1><svg width="10" height="10"><rect width="10" height="10"/></svg>
<script>document.body.dataset.ready = "1";</script></body>
</html>
""".encode()


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    return tmp_path / "reports"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    return tmp_path / "config"


@pytest.fixture
def make_settings(reports_dir: Path, config_dir: Path) -> Callable[..., Settings]:
    def _make(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "reports_dir": reports_dir,
            "config_dir": config_dir,
            "upload_api_key": API_KEY,
            "public_base_url": BASE_URL,
        }
        values.update(overrides)
        return Settings(**values)

    return _make


@pytest.fixture
def make_client(make_settings: Callable[..., Settings]) -> Iterator[Callable[..., TestClient]]:
    """Start an app (running its lifespan) with the given settings overrides."""
    clients: list[TestClient] = []

    def _make(**overrides: object) -> TestClient:
        client = TestClient(create_app(make_settings(**overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def upload() -> Callable[..., Any]:
    def _upload(
        client: TestClient,
        filename: str = "performance-report.html",
        content: bytes = SAMPLE_HTML,
        *,
        name: str | None = None,
        api_key: str | None = API_KEY,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = dict(headers or {})
        if api_key is not None:
            request_headers["X-API-Key"] = api_key
        data = {"name": name} if name is not None else None
        return client.post(
            "/api/docs",
            files={"file": (filename, content, "text/html")},
            data=data,
            headers=request_headers,
        )

    return _upload


def stored_files(directory: Path) -> list[str]:
    """Every entry in the reports directory, including hidden temp files."""
    return sorted(path.name for path in directory.iterdir()) if directory.exists() else []
