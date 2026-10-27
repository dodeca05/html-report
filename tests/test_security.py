import pytest

from conftest import stored_files


# --- API key -----------------------------------------------------------------


def test_rejects_missing_api_key(client, upload, reports_dir):
    response = upload(client, api_key=None)

    assert response.status_code == 401
    assert stored_files(reports_dir) == []


def test_rejects_wrong_api_key(client, upload, reports_dir):
    response = upload(client, api_key="wrong-key")

    assert response.status_code == 401
    assert stored_files(reports_dir) == []


def test_upload_disabled_without_configured_key(make_client, upload, reports_dir):
    client = make_client(upload_api_key=None)

    assert upload(client).status_code == 503
    assert upload(client, api_key="").status_code == 503
    assert stored_files(reports_dir) == []


def test_viewing_needs_no_api_key(client, upload):
    upload(client)

    assert client.get("/docs/performance-report").status_code == 200
    assert client.get("/index").status_code == 200


# --- File type ---------------------------------------------------------------


@pytest.mark.parametrize("filename", ["report.txt", "report.htm", "report", "report.html.exe", "image.svg", ".html"])
def test_rejects_non_html_files(client, upload, reports_dir, filename):
    response = upload(client, filename)

    assert response.status_code in (400, 415)
    assert stored_files(reports_dir) == []


def test_rejects_non_html_file_even_with_html_name(client, upload, reports_dir):
    response = upload(client, "payload.js", name="looks-fine.html")

    assert response.status_code == 415
    assert stored_files(reports_dir) == []


# --- Path traversal on upload ------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    ["../evil.html", "../../etc/evil.html", "sub/evil.html", "/tmp/evil.html", "..\\evil.html", "a\\b.html", "..html"],
)
def test_rejects_dangerous_filenames(client, upload, reports_dir, tmp_path, filename):
    response = upload(client, filename)

    assert response.status_code == 400
    assert stored_files(reports_dir) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reports"]


@pytest.mark.parametrize(
    "name",
    ["../evil", "../../etc/passwd", "a/b", "a\\b", "..", "x..y", ".hidden", "trailing.", "nul\x00byte",
     "new\nline", "bidi‮override", "C:evil", "what?", "a" * 201],
)
def test_rejects_dangerous_name_field(client, upload, reports_dir, tmp_path, name):
    response = upload(client, "report.html", name=name)

    assert response.status_code == 400
    assert stored_files(reports_dir) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reports"]


# --- Path traversal on read --------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/docs/..%2Fsecret",
        "/docs/%2E%2E%2Fsecret",
        "/docs/..%5Csecret",
        "/docs/%2E%2E%2F%2E%2E%2Fsecret",
        "/docs/%2E%2E",
        "/docs/.secret",
    ],
)
def test_cannot_read_files_outside_reports_dir(client, tmp_path, path):
    (tmp_path / "secret.html").write_text("TOP SECRET")

    response = client.get(path)

    assert response.status_code == 404
    assert "TOP SECRET" not in response.text


def test_symlinks_are_not_served_or_listed(client, reports_dir, tmp_path):
    (tmp_path / "secret.html").write_text("TOP SECRET")
    (reports_dir / "link.html").symlink_to(tmp_path / "secret.html")

    assert client.get("/docs/link").status_code == 404
    assert 'href="docs/link"' not in client.get("/index").text


# --- Isolation of uploaded HTML ----------------------------------------------


def test_reports_are_sandboxed(client, upload):
    upload(client)

    response = client.get("/docs/performance-report")

    csp = response.headers["content-security-policy"]
    directives = csp.split()
    assert directives[0] == "sandbox"
    assert "allow-scripts" in directives
    assert "allow-same-origin" not in directives
    assert response.headers["x-content-type-options"] == "nosniff"


def test_index_has_restrictive_csp(client):
    csp = client.get("/index").headers["content-security-policy"]

    assert "default-src 'none'" in csp
    assert "script-src" not in csp
