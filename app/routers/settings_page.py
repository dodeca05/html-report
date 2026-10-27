"""Settings page (/settings): admin login, token authentication on/off, and API token management.

Only holders of the admin key (UPLOAD_API_KEY) can sign in. The session is a signed, HttpOnly,
SameSite=Strict cookie scoped to /settings. Every state-changing form also carries a CSRF token and
is checked against the Origin header, so neither other sites nor sandboxed reports (Origin: null)
can submit it.
"""

import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.dependencies import get_settings, get_tokens
from app.services.token_service import EXPIRY_CHOICES, ApiToken, TokenError, TokenStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

SESSION_COOKIE = "hrs_settings_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_EXPIRY = "30d"
SETTINGS_HEADERS = {
    # No scripts, forms post only to this site, and the page cannot be framed (clickjacking).
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; img-src 'self' data:; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}
_FLASH: dict[str, tuple[str, str]] = {
    "login": ("success", "로그인했습니다."),
    "logout": ("info", "로그아웃했습니다."),
    "auth-on": ("success", "토큰 인증을 켰습니다. 이제 업로드 · 교체 · 삭제에 토큰이 필요합니다."),
    "auth-off": ("warn", "토큰 인증을 껐습니다. 이제 누구나 업로드 · 교체 · 삭제할 수 있습니다."),
    "deleted": ("success", "토큰을 삭제했습니다."),
    "not-found": ("error", "삭제할 토큰을 찾을 수 없습니다."),
    "session": ("error", "로그인이 필요합니다. 다시 로그인하세요."),
}
_SAVE_FAILED = ("error", "설정 파일을 저장하지 못했습니다. 서버 로그를 확인하세요.")

SettingsDep = Annotated[Settings, Depends(get_settings)]
TokensDep = Annotated[TokenStore, Depends(get_tokens)]


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request, settings: SettingsDep, tokens: TokensDep, msg: str | None = None) -> Response:
    if settings.upload_api_key is None:
        return _render(request, "login.html", {"disabled": True}, status.HTTP_503_SERVICE_UNAVAILABLE)
    flash = _FLASH.get(msg or "")
    if _current_session(request, settings) is None:
        return _render(request, "login.html", {"flash": flash})
    return _render_settings(request, settings, tokens, flash=flash)


@router.post("/login")
def login(request: Request, settings: SettingsDep, key: Annotated[str, Form()] = "") -> Response:
    if settings.upload_api_key is None:
        return _render(request, "login.html", {"disabled": True}, status.HTTP_503_SERVICE_UNAVAILABLE)
    if not _origin_allowed(request, settings):
        return _render(request, "login.html", {"forbidden": True}, status.HTTP_403_FORBIDDEN)
    if not secrets.compare_digest(key.encode(), settings.upload_api_key.encode()):
        logger.warning("Failed settings login from %s", _client_host(request))
        flash = ("error", "관리자 키가 올바르지 않습니다.")
        return _render(request, "login.html", {"flash": flash}, status.HTTP_401_UNAUTHORIZED)

    logger.info("Settings login from %s", _client_host(request))
    response = _redirect(request, "login")
    response.set_cookie(
        SESSION_COOKIE,
        _new_session(_session_secret(settings)),
        max_age=SESSION_TTL_SECONDS,
        path=_cookie_path(request),
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/logout")
def logout(request: Request, settings: SettingsDep, csrf_token: Annotated[str, Form()] = "") -> Response:
    if (rejected := _authorize(request, settings, csrf_token)) is not None:
        return rejected
    response = _redirect(request, "logout")
    response.delete_cookie(SESSION_COOKIE, path=_cookie_path(request), httponly=True, samesite="strict")
    return response


@router.post("/token-auth")
def set_token_auth(
    request: Request,
    settings: SettingsDep,
    tokens: TokensDep,
    enabled: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if (rejected := _authorize(request, settings, csrf_token)) is not None:
        return rejected
    if enabled not in ("on", "off"):
        flash = ("error", "잘못된 요청입니다.")
        return _render_settings(request, settings, tokens, flash=flash, status_code=status.HTTP_400_BAD_REQUEST)
    try:
        tokens.set_auth_enabled(enabled == "on")
    except OSError:
        logger.exception("Failed to save %s", tokens.settings_path)
        return _render_settings(request, settings, tokens, flash=_SAVE_FAILED, status_code=500)

    if enabled == "on":
        logger.info("Token authentication turned ON from %s", _client_host(request))
    else:
        logger.warning("Token authentication turned OFF from %s: uploads are open", _client_host(request))
    return _redirect(request, f"auth-{enabled}")


@router.post("/tokens")
def create_token(
    request: Request,
    settings: SettingsDep,
    tokens: TokensDep,
    name: Annotated[str, Form()] = "",
    expiry: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if (rejected := _authorize(request, settings, csrf_token)) is not None:
        return rejected
    form = {"name": name, "expiry": expiry}
    try:
        token = tokens.create(name, expiry)
    except TokenError as exc:
        return _render_settings(
            request, settings, tokens, flash=("error", str(exc)), form=form, status_code=status.HTTP_400_BAD_REQUEST
        )
    except OSError:
        logger.exception("Failed to save %s", tokens.tokens_path)
        return _render_settings(request, settings, tokens, flash=_SAVE_FAILED, form=form, status_code=500)

    logger.info(
        "Created API token %r (expires %s) from %s", token.name, token.expires_at.isoformat(), _client_host(request)
    )
    return _render_settings(request, settings, tokens, new_token=token)


@router.post("/tokens/delete")
def delete_token(
    request: Request,
    settings: SettingsDep,
    tokens: TokensDep,
    name: Annotated[str, Form()] = "",
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if (rejected := _authorize(request, settings, csrf_token)) is not None:
        return rejected
    try:
        tokens.delete(name)
    except TokenError:
        return _redirect(request, "not-found")
    except OSError:
        logger.exception("Failed to save %s", tokens.tokens_path)
        return _render_settings(request, settings, tokens, flash=_SAVE_FAILED, status_code=500)
    logger.info("Deleted API token %r from %s", name, _client_host(request))
    return _redirect(request, "deleted")


# --- Rendering -------------------------------------------------------------------


def _render(request: Request, template: str, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        template,
        {"root_path": _root_path(request), **context},
        status_code=status_code,
        headers=SETTINGS_HEADERS,
    )


def _render_settings(
    request: Request,
    settings: Settings,
    tokens: TokenStore,
    *,
    flash: tuple[str, str] | None = None,
    new_token: ApiToken | None = None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    session = _current_session(request, settings) or ""
    now = tokens.now()
    rows = [
        {
            "name": token.name,
            "token": token.token,
            "masked": token.masked,
            "expires_at": _format_expiry(token, now),
            "expired": token.is_expired(now),
        }
        for token in tokens.list_tokens()
    ]
    context = {
        "flash": flash,
        "csrf_token": _csrf_token(_session_secret(settings), session),
        "auth_enabled": tokens.auth_enabled(),
        "tokens": rows,
        "new_token": (
            {"name": new_token.name, "token": new_token.token, "expires_at": _format_expiry(new_token, now)}
            if new_token
            else None
        ),
        "expiry_choices": EXPIRY_CHOICES,
        "form": form or {"name": "", "expiry": DEFAULT_EXPIRY},
        "tokens_path": str(tokens.tokens_path),
    }
    return _render(request, "settings.html", context, status_code)


def _format_expiry(token: ApiToken, now: datetime) -> str:
    if token.expires_at.year - now.year >= 100:
        return f"영구 ({token.expires_at:%Y-%m-%d})"
    return f"{token.expires_at:%Y-%m-%d %H:%M}"


def _redirect(request: Request, msg: str) -> RedirectResponse:
    return RedirectResponse(f"{_root_path(request)}/settings?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)


# --- Session and CSRF ----------------------------------------------------------------


def _authorize(request: Request, settings: Settings, csrf_token: str) -> Response | None:
    """Return a response that rejects the form post, or None if it may proceed."""
    if settings.upload_api_key is None:
        return _render(request, "login.html", {"disabled": True}, status.HTTP_503_SERVICE_UNAVAILABLE)
    session = _current_session(request, settings)
    if session is None:
        return _redirect(request, "session")
    expected = _csrf_token(_session_secret(settings), session)
    if not _origin_allowed(request, settings) or not secrets.compare_digest(csrf_token.encode(), expected.encode()):
        logger.warning("Rejected settings form from %s: bad CSRF token or origin", _client_host(request))
        return _render(request, "login.html", {"forbidden": True}, status.HTTP_403_FORBIDDEN)
    return None


def _session_secret(settings: Settings) -> bytes:
    # Derived from the admin key, so changing UPLOAD_API_KEY signs everyone out.
    admin_key = settings.upload_api_key or ""
    return hashlib.sha256(b"html-report-server/settings-session\0" + admin_key.encode()).digest()


def _sign(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def _new_session(secret: bytes) -> str:
    payload = f"{int(time.time()) + SESSION_TTL_SECONDS}.{secrets.token_urlsafe(16)}"
    return f"{payload}.{_sign(secret, payload)}"


def _current_session(request: Request, settings: Settings) -> str | None:
    if settings.upload_api_key is None:
        return None
    value = request.cookies.get(SESSION_COOKIE, "")
    payload, _, signature = value.rpartition(".")
    expires = payload.split(".", 1)[0]
    if not payload or not hmac.compare_digest(signature, _sign(_session_secret(settings), payload)):
        return None
    if not expires.isdigit() or int(expires) <= time.time():
        return None
    return value


def _csrf_token(secret: bytes, session: str) -> str:
    return _sign(secret, f"csrf:{session}")


def _origin_allowed(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        # Browsers send Origin on form posts; without it the CSRF token alone protects the request.
        return True
    allowed = {f"{request.url.scheme}://{request.headers.get('host', '')}".lower()}
    if settings.public_base_url:
        parts = urlsplit(settings.public_base_url)
        allowed.add(f"{parts.scheme}://{parts.netloc}".lower())
    return origin.lower() in allowed


def _root_path(request: Request) -> str:
    return request.scope.get("root_path", "")


def _cookie_path(request: Request) -> str:
    return f"{_root_path(request)}/settings"


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"
