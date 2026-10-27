import os
import re
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from conftest import API_KEY, BASE_URL, SAMPLE_HTML

SEOUL = ZoneInfo("Asia/Seoul")


def _write(directory, name, content="<p>x</p>", *, mtime=None):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def _names(response):
    return [report["name"] for report in response.json()["reports"]]


def test_empty_list(client):
    response = client.get("/api/docs")

    assert response.status_code == 200
    assert response.json() == {"count": 0, "reports": []}


def test_lists_report_fields(client, reports_dir):
    _write(reports_dir, "performance-report.html", "<h1>성능</h1>", mtime=datetime(2026, 10, 27, 23, 0, tzinfo=SEOUL))

    body = client.get("/api/docs").json()

    assert body == {
        "count": 1,
        "reports": [
            {
                "name": "performance-report",
                "filename": "performance-report.html",
                "url": f"{BASE_URL}/docs/performance-report",
                "size": len("<h1>성능</h1>".encode()),
                "uploaded_at": "2026-10-27T23:00:00+09:00",
            }
        ],
    }


def test_newest_first_and_same_order_as_index(client, reports_dir):
    _write(reports_dir, "weekly-report.html", mtime=datetime(2026, 10, 25, 9, 15, tzinfo=SEOUL))
    _write(reports_dir, "performance-report.html", mtime=datetime(2026, 10, 27, 23, 0, tzinfo=SEOUL))
    _write(reports_dir, "analysis-2026.html", mtime=datetime(2026, 10, 26, 18, 30, tzinfo=SEOUL))

    names = _names(client.get("/api/docs"))

    assert names == ["performance-report", "analysis-2026", "weekly-report"]
    index_names = re.findall(r'<td class="name"><a href="[^"]*">([^<]*)</a></td>', client.get("/index").text)
    assert names == index_names


def test_only_servable_html_files_are_listed(client, reports_dir, tmp_path):
    _write(reports_dir, "report.html")
    _write(reports_dir, "notes.txt")
    _write(reports_dir, "UPPER.HTML")
    _write(reports_dir, ".hidden.html")
    _write(reports_dir, ".upload-abc.tmp")
    (reports_dir / "folder.html").mkdir()
    (tmp_path / "secret.html").write_text("TOP SECRET")
    (reports_dir / "link.html").symlink_to(tmp_path / "secret.html")

    assert _names(client.get("/api/docs")) == ["report"]


def test_korean_names_have_encoded_urls(client, upload):
    upload(client, name="주간 보고서")

    report = client.get("/api/docs").json()["reports"][0]

    assert report["name"] == "주간 보고서"
    assert report["filename"] == "주간 보고서.html"
    assert report["url"] == f"{BASE_URL}/docs/{quote('주간 보고서', safe='')}"


def test_list_is_public(make_client, reports_dir):
    _write(reports_dir, "report.html")

    for client in (make_client(), make_client(upload_api_key=None)):
        response = client.get("/api/docs")
        assert response.status_code == 200
        assert _names(response) == ["report"]


def test_list_url_does_not_reflect_untrusted_host(make_client, reports_dir):
    client = make_client(public_base_url=None, port=9000)
    _write(reports_dir, "report.html")

    response = client.get("/api/docs", headers={"Host": "evil.example"})

    assert response.json()["reports"][0]["url"] == "http://localhost:9000/docs/report"


def test_list_reflects_upload_replace_and_delete(client, upload, reports_dir):
    upload(client, "first.html")
    upload(client, "second.html")
    os.utime(reports_dir / "second.html", (1_700_000_000, 1_700_000_000))
    headers = {"X-API-Key": API_KEY}

    client.put("/api/docs/second", files={"file": ("x.html", b"<p>longer content</p>", "text/html")}, headers=headers)
    reports = client.get("/api/docs").json()["reports"]
    assert [r["name"] for r in reports] == ["second", "first"]
    assert reports[0]["size"] == len(b"<p>longer content</p>")
    assert reports[1]["size"] == len(SAMPLE_HTML)

    client.delete("/api/docs/second", headers=headers)
    assert _names(client.get("/api/docs")) == ["first"]


def test_openapi_documents_list_endpoint(client):
    operations = client.get("/api/openapi.json").json()["paths"]["/api/docs"]

    assert {"get", "post"} <= set(operations)
    assert "security" not in operations["get"]
