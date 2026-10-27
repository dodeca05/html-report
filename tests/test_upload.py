from datetime import datetime, timedelta
from urllib.parse import quote

from conftest import BASE_URL, SAMPLE_HTML, stored_files


def test_upload_returns_201_with_dto(client, upload):
    response = upload(client)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"filename", "url", "uploaded_at"}
    assert body["filename"] == "performance-report.html"
    assert body["url"] == f"{BASE_URL}/docs/performance-report"
    uploaded_at = datetime.fromisoformat(body["uploaded_at"])
    assert uploaded_at.utcoffset() == timedelta(hours=9)
    assert abs((datetime.now(uploaded_at.tzinfo) - uploaded_at).total_seconds()) < 60


def test_upload_writes_original_bytes_to_disk(client, upload, reports_dir):
    upload(client)

    assert (reports_dir / "performance-report.html").read_bytes() == SAMPLE_HTML
    assert stored_files(reports_dir) == ["performance-report.html"]


def test_name_field_overrides_original_filename(client, upload, reports_dir):
    response = upload(client, "report.html", name="weekly-analysis")

    assert response.status_code == 201
    assert response.json()["filename"] == "weekly-analysis.html"
    assert response.json()["url"] == f"{BASE_URL}/docs/weekly-analysis"
    assert stored_files(reports_dir) == ["weekly-analysis.html"]


def test_blank_name_field_falls_back_to_filename(client, upload):
    response = upload(client, "report.html", name="   ")

    assert response.status_code == 201
    assert response.json()["filename"] == "report.html"


def test_html_extension_is_normalized(client, upload, reports_dir):
    assert upload(client, "Upper.HTML").json()["filename"] == "Upper.html"
    assert upload(client, "x.html", name="named.HTML").json()["filename"] == "named.html"
    assert upload(client, "y.html", name="plain").json()["filename"] == "plain.html"
    assert stored_files(reports_dir) == ["Upper.html", "named.html", "plain.html"]


def test_korean_and_space_names_are_url_encoded(client, upload, reports_dir):
    response = upload(client, name="주간 보고서 2026")

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "주간 보고서 2026.html"
    assert body["url"] == f"{BASE_URL}/docs/{quote('주간 보고서 2026', safe='')}"
    assert body["url"].endswith("/docs/%EC%A3%BC%EA%B0%84%20%EB%B3%B4%EA%B3%A0%EC%84%9C%202026")
    assert (reports_dir / "주간 보고서 2026.html").exists()

    path = body["url"].removeprefix(BASE_URL)
    assert client.get(path).content == SAMPLE_HTML


def test_decomposed_hangul_is_stored_composed(client, upload, reports_dir):
    # macOS may send NFD file names; browsers request NFC.
    decomposed = "보고서"  # "보고서" as conjoining jamo
    response = upload(client, f"{decomposed}.html")

    assert response.json()["filename"] == "보고서.html"
    assert client.get(f"/docs/{quote('보고서')}").status_code == 200


def test_rejects_duplicate_name_without_overwriting(client, upload, reports_dir):
    assert upload(client).status_code == 201

    response = upload(client, content=b"<html>replacement</html>")

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]
    assert (reports_dir / "performance-report.html").read_bytes() == SAMPLE_HTML


def test_duplicate_detection_uses_the_normalized_name(client, upload):
    assert upload(client, "report.html").status_code == 201
    assert upload(client, "other.html", name="report.HTML").status_code == 409


def test_rejects_file_over_size_limit(make_client, upload, reports_dir):
    client = make_client(max_upload_size_mb=0.001)  # 1048 bytes

    response = upload(client, content=b"<p>" + b"a" * 2000 + b"</p>")

    assert response.status_code == 413
    assert stored_files(reports_dir) == []


def test_accepts_file_exactly_at_size_limit(make_client, upload):
    client = make_client(max_upload_size_mb=0.001)

    response = upload(client, content=b"a" * 1048)

    assert response.status_code == 201


def test_rejects_oversized_body_before_parsing(make_client, upload, reports_dir):
    client = make_client(max_upload_size_mb=0.001)

    response = upload(client, content=b"a" * 200_000)

    assert response.status_code == 413
    assert response.json()["detail"] == "File is too large. Maximum upload size is 0.001 MB."
    assert stored_files(reports_dir) == []


def test_rejects_oversized_chunked_body(make_client, reports_dir):
    client = make_client(max_upload_size_mb=0.001)

    def chunks():
        yield b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.html\"\r\n\r\n"
        for _ in range(20):
            yield b"a" * 10_000

    response = client.post(
        "/api/docs",
        content=chunks(),
        headers={"X-API-Key": "test-api-key", "Content-Type": "multipart/form-data; boundary=b"},
    )

    assert response.status_code == 413
    assert stored_files(reports_dir) == []


def test_rejects_empty_file(client, upload, reports_dir):
    response = upload(client, content=b"")

    assert response.status_code == 400
    assert stored_files(reports_dir) == []


def test_rejects_non_utf8_content(client, upload, reports_dir):
    response = upload(client, content="<p>한글</p>".encode("euc-kr"))

    assert response.status_code == 400
    assert "UTF-8" in response.json()["detail"]
    assert stored_files(reports_dir) == []


def test_url_uses_allowed_host_when_public_base_url_unset(make_client, upload):
    client = make_client(public_base_url=None)

    response = upload(client, headers={"Host": "localhost:8000"})

    assert response.json()["url"] == "http://localhost:8000/docs/performance-report"


def test_untrusted_host_header_is_not_reflected(make_client, upload):
    client = make_client(public_base_url=None, port=9000)

    response = upload(client, headers={"Host": "evil.example"})

    assert response.status_code == 201
    assert response.json()["url"] == "http://localhost:9000/docs/performance-report"


def test_public_base_url_ignores_host_header(client, upload):
    response = upload(client, headers={"Host": "evil.example"})

    assert response.json()["url"] == f"{BASE_URL}/docs/performance-report"
