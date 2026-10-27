import os
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest

from conftest import API_KEY, BASE_URL, SAMPLE_HTML, stored_files

NEW_HTML = "<!doctype html><html><body><h1>수정된 보고서</h1></body></html>".encode()


@pytest.fixture
def replace():
    def _replace(client, name, content=NEW_HTML, *, filename="report.html", api_key=API_KEY):
        headers = {"X-API-Key": api_key} if api_key is not None else {}
        return client.put(f"/api/docs/{name}", files={"file": (filename, content, "text/html")}, headers=headers)

    return _replace


@pytest.fixture
def delete():
    def _delete(client, name, *, api_key=API_KEY):
        headers = {"X-API-Key": api_key} if api_key is not None else {}
        return client.delete(f"/api/docs/{name}", headers=headers)

    return _delete


def _set_mtime(path, when):
    os.utime(path, (when.timestamp(), when.timestamp()))


# --- PUT /api/docs/{name} -----------------------------------------------------


def test_replace_overwrites_content_and_keeps_url(client, upload, replace, reports_dir):
    original = upload(client).json()

    response = replace(client, "performance-report")

    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "performance-report.html"
    assert body["url"] == original["url"] == f"{BASE_URL}/docs/performance-report"
    assert (reports_dir / "performance-report.html").read_bytes() == NEW_HTML
    assert client.get("/docs/performance-report").content == NEW_HTML
    assert stored_files(reports_dir) == ["performance-report.html"]


def test_replace_updates_upload_date_and_index_order(client, upload, replace, reports_dir):
    seoul = ZoneInfo("Asia/Seoul")
    upload(client, "older.html")
    upload(client, "newer.html")
    _set_mtime(reports_dir / "older.html", datetime(2026, 1, 1, 9, 0, tzinfo=seoul))
    _set_mtime(reports_dir / "newer.html", datetime(2026, 2, 1, 9, 0, tzinfo=seoul))

    uploaded_at = datetime.fromisoformat(replace(client, "older").json()["uploaded_at"])

    assert uploaded_at > datetime(2026, 2, 1, 9, 0, tzinfo=seoul)
    html = client.get("/index").text
    assert html.index(">older<") < html.index(">newer<")
    assert "2026-01-01 09:00:00" not in html


def test_replace_works_with_encoded_korean_name(client, upload, replace):
    upload(client, name="주간 보고서")

    response = replace(client, quote("주간 보고서"))

    assert response.status_code == 200
    assert response.json()["filename"] == "주간 보고서.html"
    assert client.get(f"/docs/{quote('주간 보고서')}").content == NEW_HTML


def test_replace_missing_report_returns_404_without_creating(client, replace, reports_dir):
    response = replace(client, "does-not-exist")

    assert response.status_code == 404
    assert stored_files(reports_dir) == []


@pytest.mark.parametrize(
    ("kwargs", "expected_status"),
    [
        ({"api_key": None}, 401),
        ({"api_key": "wrong-key"}, 401),
        ({"filename": "report.txt"}, 415),
        ({"content": b""}, 400),
        ({"content": "<p>한글</p>".encode("euc-kr")}, 400),
    ],
)
def test_rejected_replace_keeps_original(client, upload, replace, reports_dir, kwargs, expected_status):
    upload(client)

    response = replace(client, "performance-report", **kwargs)

    assert response.status_code == expected_status
    assert (reports_dir / "performance-report.html").read_bytes() == SAMPLE_HTML
    assert stored_files(reports_dir) == ["performance-report.html"]


def test_replace_over_size_limit_keeps_original(make_client, upload, replace, reports_dir):
    client = make_client(max_upload_size_mb=0.001)
    upload(client, content=b"<p>small</p>")

    response = replace(client, "performance-report", content=b"a" * 2000)

    assert response.status_code == 413
    assert (reports_dir / "performance-report.html").read_bytes() == b"<p>small</p>"
    assert stored_files(reports_dir) == ["performance-report.html"]


def test_replace_disabled_without_configured_key(make_client, replace, reports_dir):
    client = make_client(upload_api_key=None)
    reports_dir.joinpath("report.html").write_bytes(SAMPLE_HTML)

    assert replace(client, "report").status_code == 503
    assert reports_dir.joinpath("report.html").read_bytes() == SAMPLE_HTML


@pytest.mark.parametrize("name", ["..%2Fsecret", "%2E%2E%2Fsecret", "..%5Csecret", ".secret"])
def test_replace_cannot_touch_files_outside_reports_dir(client, replace, tmp_path, name):
    (tmp_path / "secret.html").write_text("TOP SECRET")

    response = replace(client, name)

    assert response.status_code == 404
    assert (tmp_path / "secret.html").read_text() == "TOP SECRET"


def test_replace_does_not_follow_symlinks(client, replace, reports_dir, tmp_path):
    (tmp_path / "secret.html").write_text("TOP SECRET")
    (reports_dir / "link.html").symlink_to(tmp_path / "secret.html")

    assert replace(client, "link").status_code == 404
    assert (tmp_path / "secret.html").read_text() == "TOP SECRET"
    assert (reports_dir / "link.html").is_symlink()


# --- DELETE /api/docs/{name} --------------------------------------------------


def test_delete_removes_report(client, upload, delete, reports_dir):
    upload(client)

    response = delete(client, "performance-report")

    assert response.status_code == 204
    assert response.content == b""
    assert stored_files(reports_dir) == []
    assert client.get("/docs/performance-report").status_code == 404
    assert "업로드된 보고서가 없습니다" in client.get("/index").text


def test_delete_then_upload_same_name(client, upload, delete):
    upload(client)
    delete(client, "performance-report")

    assert upload(client, content=NEW_HTML).status_code == 201
    assert client.get("/docs/performance-report").content == NEW_HTML


def test_delete_works_with_encoded_korean_name(client, upload, delete, reports_dir):
    upload(client, name="주간 보고서")

    assert delete(client, quote("주간 보고서")).status_code == 204
    assert stored_files(reports_dir) == []


def test_delete_missing_report_returns_404(client, delete):
    assert delete(client, "does-not-exist").status_code == 404


@pytest.mark.parametrize("api_key", [None, "wrong-key"])
def test_delete_requires_api_key(client, upload, delete, reports_dir, api_key):
    upload(client)

    assert delete(client, "performance-report", api_key=api_key).status_code == 401
    assert stored_files(reports_dir) == ["performance-report.html"]


def test_delete_disabled_without_configured_key(make_client, delete, reports_dir):
    client = make_client(upload_api_key=None)
    reports_dir.joinpath("report.html").write_bytes(SAMPLE_HTML)

    assert delete(client, "report").status_code == 503
    assert reports_dir.joinpath("report.html").exists()


@pytest.mark.parametrize("name", ["..%2Fsecret", "%2E%2E%2Fsecret", "..%5Csecret", ".secret"])
def test_delete_cannot_touch_files_outside_reports_dir(client, delete, tmp_path, reports_dir, name):
    (tmp_path / "secret.html").write_text("TOP SECRET")
    (reports_dir / ".secret.html").write_text("hidden")

    assert delete(client, name).status_code == 404
    assert (tmp_path / "secret.html").exists()
    assert (reports_dir / ".secret.html").exists()


def test_delete_does_not_follow_symlinks(client, delete, reports_dir, tmp_path):
    (tmp_path / "secret.html").write_text("TOP SECRET")
    (reports_dir / "link.html").symlink_to(tmp_path / "secret.html")

    assert delete(client, "link").status_code == 404
    assert (tmp_path / "secret.html").exists()
    assert (reports_dir / "link.html").is_symlink()


# --- Documentation --------------------------------------------------------------


def test_openapi_documents_replace_and_delete(client):
    paths = client.get("/api/openapi.json").json()["paths"]

    assert {"put", "delete"} <= set(paths["/api/docs/{name}"])
