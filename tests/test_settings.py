import csv
import json
import re
import stat
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.routers import settings_page
from app.services.token_service import TOKEN_PREFIX, compute_expiry
from conftest import API_KEY, BASE_URL, stored_files

SEOUL = ZoneInfo("Asia/Seoul")
SAME_ORIGIN = "http://testserver"


def login(client, key=API_KEY, origin=SAME_ORIGIN):
    headers = {"Origin": origin} if origin else {}
    return client.post("/settings/login", data={"key": key}, headers=headers, follow_redirects=False)


def csrf_token(client):
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/settings").text)
    assert match, "settings page has no CSRF token (not logged in?)"
    return match.group(1)


def submit(client, path, data=None, *, origin=SAME_ORIGIN, csrf=True):
    payload = dict(data or {})
    if csrf:
        payload["csrf_token"] = csrf_token(client)
    headers = {"Origin": origin} if origin else {}
    return client.post(path, data=payload, headers=headers, follow_redirects=False)


def create_token(client, name="agent", expiry="30d"):
    return submit(client, "/settings/tokens", {"name": name, "expiry": expiry})


def read_csv(config_dir):
    with (config_dir / "tokens.csv").open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(config_dir, rows):
    config_dir.mkdir(parents=True, exist_ok=True)
    lines = ["name,token,expires_at", *(",".join(row) for row in rows)]
    (config_dir / "tokens.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def upload_with(client, upload, key, name="performance-report"):
    return upload(client, f"{name}.html", api_key=key)


@pytest.fixture
def admin(client):
    """A client that is signed in to the settings page."""
    assert login(client).status_code == 303
    return client


# --- Access to the settings page ------------------------------------------------


def test_index_links_to_settings(client):
    assert 'href="/settings"' in client.get("/index").text


def test_settings_requires_login(client):
    response = client.get("/settings")

    assert response.status_code == 200
    assert 'name="key"' in response.text
    assert "csrf_token" not in response.text


def test_settings_disabled_without_admin_key(make_client):
    client = make_client(upload_api_key=None)

    response = client.get("/settings")

    assert response.status_code == 503
    assert "UPLOAD_API_KEY" in response.text
    assert login(client).status_code == 503


def test_login_with_wrong_key_is_rejected(client):
    response = login(client, key="wrong")

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert 'name="key"' in client.get("/settings").text


def test_login_sets_restricted_session_cookie(client):
    response = login(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?msg=login"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/settings" in cookie
    page = client.get("/settings")
    assert "토큰 인증" in page.text
    assert "로그인했습니다" in client.get("/settings?msg=login").text


def test_login_from_foreign_origin_is_rejected(client):
    assert login(client, origin="http://evil.example").status_code == 403


def test_tampered_or_expired_session_is_rejected(client, make_settings):
    secret = settings_page._session_secret(make_settings())
    expired_payload = f"{int(time.time()) - 10}.nonce"
    expired = f"{expired_payload}.{settings_page._sign(secret, expired_payload)}"
    valid_payload = f"{int(time.time()) + 600}.nonce"
    tampered = f"{valid_payload}.{'0' * 64}"

    for cookie in (expired, tampered):
        page = client.get("/settings", headers={"Cookie": f"{settings_page.SESSION_COOKIE}={cookie}"})
        assert 'name="key"' in page.text


def test_settings_pages_have_security_headers(admin):
    headers = admin.get("/settings").headers

    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "script-src" not in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"


def test_logout_ends_session(admin):
    response = submit(admin, "/settings/logout")

    assert response.status_code == 303
    assert 'name="key"' in admin.get("/settings").text


# --- CSRF protection --------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"csrf": False},
        {"origin": "http://evil.example"},
        {"origin": "null"},  # sandboxed reports have an opaque origin
    ],
)
def test_forged_form_posts_are_rejected(admin, config_dir, kwargs):
    response = submit(admin, "/settings/token-auth", {"enabled": "off"}, **kwargs)

    assert response.status_code == 403
    assert not (config_dir / "settings.json").exists()


def test_form_post_without_session_redirects_to_login(client, config_dir):
    response = client.post(
        "/settings/tokens",
        data={"name": "x", "expiry": "7d", "csrf_token": "x"},
        headers={"Origin": SAME_ORIGIN},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?msg=session"
    assert not (config_dir / "tokens.csv").exists()


def test_public_base_url_origin_is_accepted(admin):
    assert submit(admin, "/settings/token-auth", {"enabled": "on"}, origin=BASE_URL).status_code == 303


# --- Token authentication on/off --------------------------------------------------


def test_token_auth_is_on_by_default(admin, upload, reports_dir):
    assert "켜짐" in admin.get("/settings").text
    assert upload(admin, api_key=None).status_code == 401
    assert stored_files(reports_dir) == []


def test_turning_token_auth_off_opens_uploads(admin, upload, config_dir):
    response = submit(admin, "/settings/token-auth", {"enabled": "off"})

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?msg=auth-off"
    assert json.loads((config_dir / "settings.json").read_text()) == {"token_auth_enabled": False}
    assert "꺼짐" in admin.get("/settings").text
    assert upload(admin, api_key=None).status_code == 201
    assert admin.delete("/api/docs/performance-report").status_code == 204


def test_turning_token_auth_back_on_requires_tokens_again(admin, upload):
    submit(admin, "/settings/token-auth", {"enabled": "off"})
    submit(admin, "/settings/token-auth", {"enabled": "on"})

    assert upload(admin, api_key=None).status_code == 401
    assert upload(admin).status_code == 201


def test_invalid_toggle_value_is_rejected(admin, config_dir):
    assert submit(admin, "/settings/token-auth", {"enabled": "maybe"}).status_code == 400
    assert not (config_dir / "settings.json").exists()


def test_corrupt_settings_file_keeps_auth_on(client, upload, config_dir):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text("{not json")

    assert upload(client, api_key=None).status_code == 401


# --- Creating tokens --------------------------------------------------------------


def test_create_token_shows_and_stores_random_value(admin, config_dir):
    response = create_token(admin, name="claude-code 노트북", expiry="30d")

    assert response.status_code == 200
    rows = read_csv(config_dir)
    assert len(rows) == 1
    row = rows[0]
    assert list(row) == ["name", "token", "expires_at"]
    assert row["name"] == "claude-code 노트북"
    assert row["token"].startswith(TOKEN_PREFIX)
    assert len(row["token"]) >= 40
    assert row["token"] in response.text
    assert "새 토큰을 만들었습니다" in response.text


def test_tokens_are_random(admin, config_dir):
    create_token(admin, name="a")
    create_token(admin, name="b")

    first, second = (row["token"] for row in read_csv(config_dir))
    assert first != second


def test_token_value_cannot_be_chosen(admin, config_dir):
    submit(admin, "/settings/tokens", {"name": "mine", "expiry": "7d", "token": "hrs_my-own-value"})

    assert read_csv(config_dir)[0]["token"] != "hrs_my-own-value"


@pytest.mark.parametrize(("choice", "days", "years"), [("7d", 7, 0), ("30d", 30, 0), ("1y", 0, 1), ("forever", 0, 999)])
def test_expiry_options(admin, config_dir, choice, days, years):
    before = datetime.now(SEOUL)

    create_token(admin, name=choice, expiry=choice)

    expires_at = datetime.fromisoformat(read_csv(config_dir)[0]["expires_at"])
    assert expires_at.utcoffset() == timedelta(hours=9)
    if days:
        assert abs((expires_at - before) - timedelta(days=days)) < timedelta(seconds=5)
    else:
        assert expires_at.year == before.year + years
        assert (expires_at.month, expires_at.day) == (before.month, before.day)


def test_expiry_labels_are_offered(admin):
    page = admin.get("/settings").text

    for label in ("7일", "30일", "1년", "영구 (999년)"):
        assert label in page


def test_forever_token_is_shown_as_permanent(admin):
    create_token(admin, name="forever", expiry="forever")

    assert "영구 (" in admin.get("/settings").text


def test_leap_day_expiry_falls_back_to_february_28():
    leap_day = datetime(2028, 2, 29, 12, 0, tzinfo=SEOUL)

    assert compute_expiry("1y", leap_day) == datetime(2029, 2, 28, 12, 0, tzinfo=SEOUL)
    assert compute_expiry("forever", leap_day).date().isoformat() == "3027-02-28"


@pytest.mark.parametrize("expiry", ["", "2y", "forever!", "999"])
def test_invalid_expiry_is_rejected(admin, config_dir, expiry):
    assert create_token(admin, expiry=expiry).status_code == 400
    assert not (config_dir / "tokens.csv").exists()


@pytest.mark.parametrize("name", ["", "   ", "a" * 51, "=SUM(A1)", "+1", "-x", "@x", "tab\tname", "new\nline"])
def test_invalid_token_names_are_rejected(admin, config_dir, name):
    assert create_token(admin, name=name).status_code == 400
    assert not (config_dir / "tokens.csv").exists()


def test_duplicate_token_name_is_rejected(admin, config_dir):
    create_token(admin, name="agent")

    response = create_token(admin, name="agent")

    assert response.status_code == 400
    assert "같은 이름" in response.text
    assert len(read_csv(config_dir)) == 1


def test_token_names_are_escaped(admin):
    create_token(admin, name="<b>bold</b>")

    page = admin.get("/settings").text
    assert "&lt;b&gt;bold&lt;/b&gt;" in page
    assert "<b>bold</b>" not in page


def test_tokens_file_is_private(admin, config_dir):
    create_token(admin)

    assert stat.S_IMODE((config_dir / "tokens.csv").stat().st_mode) == 0o600


# --- Using tokens -----------------------------------------------------------------


def test_created_token_authorizes_upload_replace_and_delete(admin, upload, config_dir):
    create_token(admin)
    token = read_csv(config_dir)[0]["token"]
    headers = {"X-API-Key": token}

    assert upload(admin, api_key=token).status_code == 201
    replaced = admin.put(
        "/api/docs/performance-report", files={"file": ("r.html", b"<p>v2</p>", "text/html")}, headers=headers
    )
    assert replaced.status_code == 200
    assert admin.delete("/api/docs/performance-report", headers=headers).status_code == 204


def test_admin_key_still_works_alongside_tokens(admin, upload):
    create_token(admin)

    assert upload(admin, api_key=API_KEY).status_code == 201


def test_unknown_token_is_rejected(admin, upload):
    create_token(admin)

    assert upload(admin, api_key=TOKEN_PREFIX + "not-a-real-token").status_code == 401


def test_expired_token_is_rejected(client, upload, config_dir, reports_dir):
    write_csv(config_dir, [("old", "hrs_expired-token-value", "2020-01-01T00:00:00+09:00")])

    response = upload(client, api_key="hrs_expired-token-value")

    assert response.status_code == 401
    assert response.json()["detail"] == "API key has expired."
    assert stored_files(reports_dir) == []


def test_expired_token_is_listed_as_expired(admin, config_dir):
    write_csv(config_dir, [("old", "hrs_expired-token-value", "2020-01-01T00:00:00+09:00")])

    assert "만료됨" in admin.get("/settings").text


def test_hand_edited_csv_is_accepted_and_bad_rows_skipped(client, upload, config_dir):
    write_csv(
        config_dir,
        [
            ("manual", "hrs_manual-token-value", "2099-12-31"),
            ("no-token", "", "2099-12-31"),
            ("bad-date", "hrs_bad-date-value", "someday"),
        ],
    )

    assert upload(client, "a.html", api_key="hrs_manual-token-value").status_code == 201
    assert upload(client, "b.html", api_key="hrs_bad-date-value").status_code == 401
    assert upload(client, "c.html", api_key="").status_code == 401


def test_tokens_work_without_admin_key(make_client, upload, config_dir):
    write_csv(config_dir, [("agent", "hrs_agent-token-value", "2099-12-31T00:00:00+09:00")])
    client = make_client(upload_api_key=None)

    assert upload(client, api_key="hrs_agent-token-value").status_code == 201
    assert client.get("/settings").status_code == 503


# --- Deleting tokens --------------------------------------------------------------


def test_delete_token_revokes_it(admin, upload, config_dir):
    create_token(admin, name="keep")
    create_token(admin, name="remove")
    removed = read_csv(config_dir)[1]["token"]

    response = submit(admin, "/settings/tokens/delete", {"name": "remove"})

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?msg=deleted"
    assert [row["name"] for row in read_csv(config_dir)] == ["keep"]
    assert upload(admin, api_key=removed).status_code == 401


def test_delete_unknown_token(admin):
    response = submit(admin, "/settings/tokens/delete", {"name": "missing"})

    assert response.headers["location"] == "/settings?msg=not-found"


# --- Persistence --------------------------------------------------------------------


def test_tokens_and_switch_survive_restart(make_client, upload, config_dir):
    first = make_client()
    login(first)
    create_token(first)
    submit(first, "/settings/token-auth", {"enabled": "off"})
    token = read_csv(config_dir)[0]["token"]
    first.__exit__(None, None, None)

    restarted = make_client()

    assert upload(restarted, "after-restart.html", api_key=None).status_code == 201
    login(restarted)
    page = restarted.get("/settings").text
    assert "꺼짐" in page
    assert token in page
