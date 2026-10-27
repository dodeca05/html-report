import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from conftest import SAMPLE_HTML

SEOUL = ZoneInfo("Asia/Seoul")


def _write_report(directory, name, content="<p>x</p>", *, mtime=None):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        timestamp = mtime.timestamp()
        os.utime(path, (timestamp, timestamp))
    return path


def _index_names(html):
    return re.findall(r'<td class="name"><a href="[^"]*">([^<]*)</a></td>', html)


# --- /docs/{name} ------------------------------------------------------------


def test_view_returns_original_html(client, upload):
    upload(client)

    response = client.get("/docs/performance-report")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.content == SAMPLE_HTML


def test_view_serves_files_placed_directly_on_disk(client, reports_dir):
    _write_report(reports_dir, "analysis-2026.html", "<h1>analysis</h1>")

    assert client.get("/docs/analysis-2026").text == "<h1>analysis</h1>"


def test_view_missing_report_returns_404(client):
    response = client.get("/docs/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Report not found."}


def test_view_does_not_append_extension_twice(client, upload):
    upload(client, "report.html")

    assert client.get("/docs/report.html").status_code == 404


# --- /index ------------------------------------------------------------------


def test_index_lists_reports_with_seoul_dates_newest_first(client, reports_dir):
    _write_report(reports_dir, "weekly-report.html", mtime=datetime(2026, 10, 25, 9, 15, tzinfo=SEOUL))
    _write_report(reports_dir, "performance-report.html", mtime=datetime(2026, 10, 27, 23, 0, tzinfo=SEOUL))
    _write_report(reports_dir, "analysis-2026.html", mtime=datetime(2026, 10, 26, 18, 30, tzinfo=SEOUL))

    response = client.get("/index")

    assert response.status_code == 200
    assert "<h1>HTML Reports</h1>" in response.text
    assert _index_names(response.text) == ["performance-report", "analysis-2026", "weekly-report"]
    assert "2026-10-27 23:00:00" in response.text
    assert "2026-10-26 18:30:00" in response.text
    assert "2026-10-25 09:15:00" in response.text
    assert 'href="docs/performance-report"' in response.text
    assert ".html</a>" not in response.text


def test_index_converts_utc_mtime_to_seoul(client, reports_dir):
    _write_report(reports_dir, "utc.html", mtime=datetime(2026, 10, 27, 14, 0, tzinfo=ZoneInfo("UTC")))

    assert "2026-10-27 23:00:00" in client.get("/index").text


def test_index_shows_uploaded_report(client, upload):
    uploaded_at = datetime.fromisoformat(upload(client).json()["uploaded_at"])

    html = client.get("/index").text

    assert _index_names(html) == ["performance-report"]
    assert uploaded_at.strftime("%Y-%m-%d %H:%M:%S") in html


def test_index_only_lists_servable_html_files(client, reports_dir):
    _write_report(reports_dir, "report.html")
    _write_report(reports_dir, "notes.txt")
    _write_report(reports_dir, "UPPER.HTML")
    _write_report(reports_dir, ".hidden.html")
    _write_report(reports_dir, ".upload-abc.tmp")
    (reports_dir / "folder.html").mkdir()

    assert _index_names(client.get("/index").text) == ["report"]


def test_index_empty_state(client):
    html = client.get("/index").text

    assert "업로드된 보고서가 없습니다" in html
    assert "<table" not in html


def test_index_escapes_names_and_encodes_links(client, upload):
    upload(client, name="R&D 'Q4' 보고서")

    html = client.get("/index").text

    assert "R&amp;D &#39;Q4&#39; 보고서" in html
    assert 'href="docs/R%26D%20%27Q4%27%20%EB%B3%B4%EA%B3%A0%EC%84%9C"' in html


# --- Routing -----------------------------------------------------------------


def test_root_redirects_to_index(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/index"


def test_bare_docs_path_redirects_to_index(client):
    response = client.get("/docs", follow_redirects=False)

    assert response.headers["location"] == "/index"


def test_swagger_moved_to_api_swagger(client):
    assert client.get("/api/swagger").status_code == 200
    assert client.get("/api/openapi.json").json()["paths"]["/api/docs"]["post"]


def test_stylesheet_is_served(client):
    assert client.get("/static/style.css").status_code == 200


# --- Persistence ---------------------------------------------------------------


def test_reports_survive_server_restart(make_client, upload):
    first = make_client()
    url = upload(first).json()["url"]
    first.__exit__(None, None, None)

    restarted = make_client()

    assert restarted.get(url.removeprefix("https://reports.example.com")).content == SAMPLE_HTML
    assert _index_names(restarted.get("/index").text) == ["performance-report"]


def test_startup_creates_missing_reports_dir(make_client, tmp_path):
    nested = tmp_path / "a" / "b" / "reports"

    make_client(reports_dir=nested)

    assert nested.is_dir()


def test_startup_removes_only_stale_temp_files(make_client, reports_dir):
    stale = _write_report(reports_dir, ".upload-stale.tmp", mtime=datetime(2020, 1, 1, tzinfo=SEOUL))
    fresh = _write_report(reports_dir, ".upload-fresh.tmp")

    make_client()

    assert not stale.exists()
    assert fresh.exists()
