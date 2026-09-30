"""
גישה מוגנת בסיסמה – לסביבת בדיקה שנגישה מהאינטרנט.

כבוי כברירת מחדל: בהרצה מקומית (127.0.0.1) אין כניסה. מופעל כשמוגדרת
סיסמה באחד מהשניים:
  * `POLIXOR_ACCESS_PASSWORD`       – הסיסמה עצמה
  * `POLIXOR_ACCESS_PASSWORD_FILE`  – נתיב לקובץ שמכיל אותה

כשהוא פעיל, **כל** בקשה עוברת דרכו: ה-API, ה-WebSocket, קובצי הווידאו,
ההורדות, קובצי הממשק ו-/docs. מחוץ לשער נשארים רק דף הכניסה עצמו
ו-/api/health (בדיקת חיים בלי מידע רגיש).

הכניסה נשמרת בעוגייה חתומה (HMAC) – HttpOnly, SameSite=Lax, ו-Secure
כשהגישה ב-HTTPS. המפתח נגזר מהסיסמה ומסוד אקראי שנשמר בתיקיית הנתונים,
ולכן החלפת סיסמה מנתקת את כל ההתחברויות הקודמות. ניסיונות כושלים
מואטים וננעלים זמנית לפי כתובת.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qs, quote

from . import i18n

log = logging.getLogger("polixor.access")

COOKIE = "polixor_session"
SESSION_DAYS = 30
MAX_BODY = 4096
FREE_ATTEMPTS = 5          # ניסיונות כושלים לפני נעילה
LOCK_BASE_SECONDS = 30.0   # נעילה ראשונה; מוכפלת בכל כישלון נוסף
LOCK_MAX_SECONDS = 300.0

PUBLIC_PATHS = frozenset({"/login", "/logout", "/api/health"})

ASGIApp = Callable[[dict, Callable, Callable], Awaitable[None]]


def configured_password() -> str:
    """הסיסמה מהסביבה, או מחרוזת ריקה כשהשער כבוי."""
    direct = os.environ.get("POLIXOR_ACCESS_PASSWORD", "")
    if direct.strip():
        return direct.strip()
    path = os.environ.get("POLIXOR_ACCESS_PASSWORD_FILE", "").strip()
    if path:
        try:
            return Path(path).expanduser().read_text("utf-8").strip()
        except OSError as exc:
            log.error("cannot read POLIXOR_ACCESS_PASSWORD_FILE: %s", exc)
    return ""


def _server_secret(data_dir: Path) -> bytes:
    """סוד אקראי קבוע לשרת הזה. נוצר פעם אחת, הרשאות 600."""
    path = data_dir / "session.key"
    try:
        raw = path.read_bytes()
        if len(raw) >= 32:
            return raw
    except OSError:
        pass
    data_dir.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(32)
    path.write_bytes(raw)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return raw


class AccessGate:
    """Middleware ASGI טהור: עובד גם על HTTP וגם על WebSocket."""

    def __init__(self, app: ASGIApp, *, password: Optional[str] = None,
                 data_dir: Optional[Path] = None) -> None:
        self.app = app
        self.password = configured_password() if password is None else password
        self.enabled = bool(self.password)
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        if self.enabled:
            from .config import PATHS

            secret = _server_secret(data_dir or PATHS.data)
            self._key = hashlib.sha256(secret + self.password.encode("utf-8")).digest()
            log.info("password protection is ON")

    # ------------------------------------------------------------------
    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        kind = scope.get("type")
        if not self.enabled or kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        if kind == "http" and path == "/login":
            await self._login(scope, receive, send)
            return
        if kind == "http" and path == "/logout":
            await self._logout(scope, send)
            return
        if path in PUBLIC_PATHS or self._authorized(scope):
            await self.app(scope, receive, send)
            return

        if kind == "websocket":
            # סגירה לפני accept = דחיית לחיצת היד (HTTP 403)
            await send({"type": "websocket.close", "code": 4401})
            return
        await self._deny(scope, send)

    # ------------------------------------------------------------------
    # סשן
    # ------------------------------------------------------------------
    def _sign(self, issued: int) -> str:
        msg = f"v1.{issued}".encode("ascii")
        sig = hmac.new(self._key, msg, hashlib.sha256).hexdigest()
        return f"v1.{issued}.{sig}"

    def _valid(self, token: str) -> bool:
        try:
            version, issued_s, _sig = token.split(".", 2)
            issued = int(issued_s)
        except ValueError:
            return False
        if version != "v1" or issued > time.time() + 60:
            return False
        if time.time() - issued > SESSION_DAYS * 86400:
            return False
        return hmac.compare_digest(self._sign(issued), token)

    def _authorized(self, scope: dict) -> bool:
        token = _cookies(scope).get(COOKIE, "")
        return bool(token) and self._valid(token)

    # ------------------------------------------------------------------
    # כניסה ויציאה
    # ------------------------------------------------------------------
    async def _login(self, scope: dict, receive: Callable, send: Callable) -> None:
        nxt = _safe_next(_query(scope).get("next", "/"))
        if scope.get("method") != "POST":
            if self._authorized(scope):
                await _redirect(send, nxt)
                return
            await _page(send, 200, login_page(nxt))
            return

        client = _client(scope)
        wait = self._locked_for(client)
        if wait > 0:
            await _page(send, 429, login_page(
                nxt, error=i18n.tr("access.locked", seconds=int(wait) + 1)))
            return

        form = parse_qs((await _read_body(receive)).decode("utf-8", "replace"))
        # רווח או שורה חדשה שנגררו בהעתקה אינם חלק מהסיסמה (גם הסיסמה
        # השמורה נקראת בלי רווחים בקצוות)
        given = (form.get("password") or [""])[0].strip()
        nxt = _safe_next((form.get("next") or [nxt])[0])
        if hmac.compare_digest(given.encode("utf-8"), self.password.encode("utf-8")):
            self._clear_failures(client)
            token = self._sign(int(time.time()))
            await _redirect(send, nxt, cookie=_cookie_header(
                token, SESSION_DAYS * 86400, secure=_is_https(scope)))
            return

        self._record_failure(client)
        await asyncio.sleep(0.6)                 # מאט ניחוש אוטומטי
        await _page(send, 401, login_page(nxt, error=i18n.tr("access.wrong")))

    async def _logout(self, scope: dict, send: Callable) -> None:
        await _redirect(send, "/login", cookie=_cookie_header(
            "", 0, secure=_is_https(scope)))

    async def _deny(self, scope: dict, send: Callable) -> None:
        path = scope.get("path") or "/"
        headers = _headers(scope)
        wants_page = (scope.get("method") == "GET"
                      and not path.startswith(("/api/", "/docs", "/openapi"))
                      and "text/html" in headers.get("accept", ""))
        if wants_page:
            query = (scope.get("query_string") or b"").decode("latin-1")
            target = path + (f"?{query}" if query else "")
            await _redirect(send, "/login?next=" + quote(target, safe=""))
            return
        body = json.dumps({"code": "auth_required",
                           "message": i18n.tr("access.required"),
                           "hint": i18n.tr("access.required_hint"),
                           "detail": ""}, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    # ------------------------------------------------------------------
    # הגבלת ניסיונות
    # ------------------------------------------------------------------
    def _locked_for(self, client: str) -> float:
        with self._lock:
            count, until = self._failures.get(client, (0, 0.0))
        return max(0.0, until - time.time()) if count >= FREE_ATTEMPTS else 0.0

    def _record_failure(self, client: str) -> None:
        with self._lock:
            count, _ = self._failures.get(client, (0, 0.0))
            count += 1
            until = 0.0
            if count >= FREE_ATTEMPTS:
                extra = count - FREE_ATTEMPTS
                until = time.time() + min(LOCK_MAX_SECONDS, LOCK_BASE_SECONDS * (2 ** extra))
                log.warning("login locked for %s after %d failures", client, count)
            self._failures[client] = (count, until)

    def _clear_failures(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)


# --------------------------------------------------------------------------
# עזרי ASGI
# --------------------------------------------------------------------------
def _headers(scope: dict) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in (scope.get("headers") or [])}


def _cookies(scope: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in _headers(scope).get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name:
            out[name] = value
    return out


def _query(scope: dict) -> dict[str, str]:
    q = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    return {k: v[0] for k, v in q.items() if v}


def _client(scope: dict) -> str:
    fwd = _headers(scope).get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    client = scope.get("client") or ("unknown", 0)
    return str(client[0])


def _is_https(scope: dict) -> bool:
    if scope.get("scheme") in ("https", "wss"):
        return True
    return _headers(scope).get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _safe_next(value: str) -> str:
    """רק נתיב יחסי באותו אתר – לא הפניה פתוחה לאתר אחר."""
    value = (value or "/").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if value.startswith(("/login", "/logout")):
        return "/"
    return value


def _cookie_header(value: str, max_age: int, *, secure: bool) -> bytes:
    parts = [f"{COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={max_age}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts).encode("latin-1")


async def _read_body(receive: Callable) -> bytes:
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if len(body) > MAX_BODY:
            return body[:MAX_BODY]
        if not message.get("more_body"):
            return body


async def _redirect(send: Callable, location: str, *, cookie: Optional[bytes] = None) -> None:
    headers = [(b"location", location.encode("latin-1")), (b"cache-control", b"no-store")]
    if cookie is not None:
        headers.append((b"set-cookie", cookie))
    await send({"type": "http.response.start", "status": 303, "headers": headers})
    await send({"type": "http.response.body", "body": b""})


async def _page(send: Callable, status: int, body: str) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/html; charset=utf-8"),
                            (b"cache-control", b"no-store"),
                            (b"x-frame-options", b"DENY")]})
    await send({"type": "http.response.body", "body": body.encode("utf-8")})


# --------------------------------------------------------------------------
# דף הכניסה
# --------------------------------------------------------------------------
def login_page(next_path: str = "/", *, error: str = "") -> str:
    lang = i18n.get_lang()
    other = "en" if lang == "he" else "he"
    t = lambda key, **kw: html.escape(i18n.tr(f"access.{key}", **kw))  # noqa: E731
    err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    nxt = html.escape(next_path, quote=True)
    return f"""<!doctype html>
<html lang="{lang}" dir="{i18n.direction(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex">
<title>{t("title")} · Polixor</title>
<style>
  :root {{ --bg:#f5f6fa; --card:#fff; --ink:#0d121e; --muted:#5c6477; --line:#d0d5e0;
          --brand:#285ad4; --bad:#cd2626; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#07080c; --card:#11131b; --ink:#eef1f6; --muted:#96a0b3; --line:#272c3a;
            --brand:#4b7bff; --bad:#f87171; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:24px 16px;
         background:var(--bg); color:var(--ink);
         font:16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Heebo, Roboto, Arial, sans-serif; }}
  main {{ width:100%; max-width:380px; background:var(--card); border:1px solid var(--line);
         border-radius:16px; padding:28px 24px; box-shadow:0 10px 30px rgba(15,23,42,.08); }}
  .logo {{ width:44px; height:44px; border-radius:12px; background:var(--brand); color:#fff;
          display:grid; place-items:center; font-weight:700; font-size:20px; margin-bottom:16px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  p {{ margin:0 0 20px; color:var(--muted); font-size:14px; }}
  label {{ display:block; font-size:14px; font-weight:600; margin-bottom:6px; }}
  input {{ width:100%; font-size:16px; padding:12px 14px; border-radius:10px;
          border:1px solid var(--line); background:transparent; color:var(--ink); }}
  input:focus {{ outline:2px solid var(--brand); outline-offset:1px; }}
  button {{ width:100%; margin-top:16px; padding:12px; border:0; border-radius:10px;
           background:var(--brand); color:#fff; font-size:16px; font-weight:600; cursor:pointer; }}
  .err {{ color:var(--bad); margin:12px 0 0; font-weight:500; }}
  .foot {{ margin:20px 0 0; font-size:13px; display:flex; justify-content:space-between; gap:12px; }}
  a {{ color:var(--brand); }}
</style>
</head>
<body>
<main>
  <div class="logo" aria-hidden="true">P</div>
  <h1>{t("title")}</h1>
  <p>{t("subtitle")}</p>
  <form method="post" action="/login">
    <input type="hidden" name="next" value="{nxt}">
    <label for="pw">{t("password")}</label>
    <input id="pw" name="password" type="password" autocomplete="current-password"
           required autofocus>
    {err}
    <button type="submit">{t("submit")}</button>
  </form>
  <div class="foot"><span>{t("private")}</span>
    <a href="/login?lang={other}&amp;next={quote(next_path, safe='')}">{t("switch")}</a></div>
</main>
</body>
</html>"""
