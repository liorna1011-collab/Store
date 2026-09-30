"""
ספק ארגז חול: מתנהג כמו פלטפורמה אמיתית – OAuth עם state ו-PKCE, טוקן
שפג ומתחדש, העלאה עם התקדמות, תזמון בפלטפורמה – אבל **לא מפרסם כלום**.
"הפוסטים" נרשמים בקובץ sandbox_posts.json בתיקיית הנתונים, כדי שאפשר
יהיה לראות מה היה מתפרסם.

מיועד לבדיקות ולהדגמה בלי פרטי מפתחים. מופעל בהגדרות (publish_sandbox).
סימולציית כשלים לבדיקות: options["sandbox_simulate"] = retry | auth | fail.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

from ...config import PATHS
from .base import (AccountInfo, Capabilities, PublishError, PublishRequest, PublishResult,
                   Provider, ProgressFn, TokenSet)

_lock = threading.Lock()
_codes: dict[str, str] = {}          # code → code_challenge
_tokens: dict[str, str] = {}         # access/refresh → "access"/"refresh"
_attempts: dict[str, int] = {}


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


class SandboxProvider(Provider):
    id = "sandbox"
    name = "Sandbox"
    scopes = ("video.upload",)

    def __init__(self, *, native_scheduling: bool = False) -> None:
        self._native = native_scheduling

    def capabilities(self) -> Capabilities:
        return Capabilities(formats=("short", "long"),
                            max_seconds={"short": 180.0, "long": 4 * 3600.0},
                            title_max=100, description_max=2200,
                            privacy=("public", "unlisted", "private"),
                            native_scheduling=self._native,
                            notes=("sandbox",))

    def configured(self) -> bool:
        return True

    # ---- OAuth (דף אישור מקומי) ----
    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        return "/api/publish/sandbox/authorize?" + urlencode(
            {"state": state, "redirect_uri": redirect_uri, "code_challenge": code_challenge})

    @staticmethod
    def grant(code_challenge: str) -> str:
        """המשתמש אישר בדף ארגז החול – מנפיקים code חד-פעמי."""
        code = "sbx_" + secrets.token_urlsafe(16)
        with _lock:
            _codes[code] = code_challenge
        return code

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        with _lock:
            challenge = _codes.pop(code, None)
        if challenge is None or _challenge(code_verifier) != challenge:
            raise PublishError("permanent", "publishing.error.oauth_failed", detail="bad code/PKCE")
        return self._new_tokens()

    def _new_tokens(self) -> TokenSet:
        a, r = "sbx_at_" + secrets.token_urlsafe(16), "sbx_rt_" + secrets.token_urlsafe(16)
        with _lock:
            _tokens[a], _tokens[r] = "access", "refresh"
        now = datetime.now(timezone.utc)
        return TokenSet(access_token=a, refresh_token=r, expires_at=now + timedelta(hours=1),
                        refresh_expires_at=now + timedelta(days=365), scopes=self.scopes)

    def refresh(self, refresh_token: str) -> TokenSet:
        with _lock:
            ok = _tokens.pop(refresh_token, None) == "refresh"
        if not ok:
            raise PublishError("auth", "publishing.error.reconnect")
        return self._new_tokens()

    def revoke(self, token: str) -> None:
        with _lock:
            _tokens.pop(token, None)

    def account_info(self, tokens: TokenSet) -> AccountInfo:
        return AccountInfo(external_id="sandbox-" + tokens.access_token[-6:],
                           display_name="Sandbox channel", handle="@sandbox")

    # ---- פרסום ----
    def validate(self, req: PublishRequest) -> list[dict[str, Any]]:
        return []

    def publish(self, tokens: TokenSet, req: PublishRequest, *,
                on_progress: ProgressFn = None) -> PublishResult:
        with _lock:
            valid = _tokens.get(tokens.access_token) == "access"
        if not valid:
            raise PublishError("auth", "publishing.error.reconnect", detail="token unknown")
        sim = str(req.options.get("sandbox_simulate") or "")
        key = f"{req.media_path}:{req.title}"
        with _lock:
            _attempts[key] = _attempts.get(key, 0) + 1
            n = _attempts[key]
        if sim == "fail":
            raise PublishError("permanent", "publishing.error.rejected",
                               params={"reason": "sandbox"})
        if sim == "auth":
            raise PublishError("auth", "publishing.error.reconnect")
        if sim == "retry" and n < 2:
            raise PublishError("retry", "publishing.error.temporary")
        for f in (0.25, 0.5, 1.0):
            if on_progress:
                on_progress(f)
        rid = "sbx_" + secrets.token_hex(6)
        post = {"id": rid, "title": req.title, "format": req.format, "privacy": req.privacy,
                "media": str(req.media_path), "duration": req.duration,
                "publish_at": req.publish_at.isoformat() if req.publish_at else None,
                "at": datetime.now(timezone.utc).isoformat()}
        _record(post)
        return PublishResult(remote_id=rid, url=f"https://sandbox.invalid/post/{rid}",
                             state="scheduled_on_platform" if req.publish_at else "published")


def _record(post: dict[str, Any]) -> None:
    path = PATHS.data / "sandbox_posts.json"
    with _lock:
        try:
            items = json.loads(path.read_text("utf-8")) if path.exists() else []
        except (OSError, json.JSONDecodeError):
            items = []
        items = (items + [post])[-200:]
        path.write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")


def recorded_posts() -> list[dict[str, Any]]:
    path = PATHS.data / "sandbox_posts.json"
    try:
        return json.loads(path.read_text("utf-8")) if path.exists() else []
    except (OSError, json.JSONDecodeError):
        return []


def reset_for_tests(access_tokens: Optional[list[str]] = None) -> None:
    with _lock:
        _codes.clear()
        _attempts.clear()
        if access_tokens is not None:
            _tokens.clear()
