"""
YouTube (YouTube Data API v3) – העלאה עם OAuth, העלאה ניתנת לחידוש, תזמון
בפלטפורמה, והצהרות נדרשות. תיעוד רשמי ותאריך הבדיקה: docs/providers/youtube.md.

  * OAuth של Google: authorization code + PKCE (S256), access_type=offline
    (refresh token), prompt=consent. הרשאות: youtube.upload (העלאה) +
    youtube.readonly (שם הערוץ). ביטול: oauth2.googleapis.com/revoke.
  * העלאה: resumable – POST ליצירת session (Location), ואז PUT בחלקים
    (כפולות של 256KB) עם Content-Range. 308 = להמשיך מהמקום שב-Range.
    תקלה זמנית באמצע → שאילתת מצב (Content-Range: bytes */N) והמשך, לא
    התחלה מחדש.
  * תזמון: status.publishAt מחייב privacyStatus=private; YouTube מפרסם
    בעצמו בזמן שנקבע – Polixor לא צריך לפעול.
  * status.selfDeclaredMadeForKids – נשלח תמיד (ברירת מחדל: לא לילדים).
    status.containsSyntheticMedia – כשהמשתמש מסמן תוכן שנוצר/שונה ב-AI.
  * Shorts: אין דגל נפרד – YouTube מסווג לבד (אנכי/ריבועי ועד 3 דקות).
  * פרויקט API שלא עבר ביקורת (audit) – כל ההעלאות פרטיות. מוצג כאזהרה
    עד שמסמנים בהגדרות שהפרויקט אושר.
  * תמונה ממוזערת – רק לסרטון ארוך, בקריאה נפרדת (thumbnails.set), וכשל בה
    לא מכשיל את הפרסום.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from ...config import SECRETS
from .base import (AccountInfo, Capabilities, PublishError, PublishRequest, PublishResult,
                   Provider, ProgressFn, TokenSet)

log = logging.getLogger("polixor.publishing.youtube")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly")
CHUNK = 8 * 1024 * 1024                     # כפולה של 256KB
MAX_RESUMES = 5
THUMB_MAX_BYTES = 2 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reason(resp: Any) -> str:
    """הסיבה מתשובת שגיאה של Google (בלי להעתיק את התשובה עצמה ליומן)."""
    try:
        err = resp.json().get("error") or {}
        errs = err.get("errors") or [{}]
        return str(errs[0].get("reason") or err.get("status") or "")[:60]
    except Exception:                                  # noqa: BLE001
        return ""


class YouTubeProvider(Provider):
    id = "youtube"
    name = "YouTube"
    scopes = SCOPES
    uses_pkce = True

    def __init__(self, *, http: Any = None, audited: bool = False, chunk: int = CHUNK) -> None:
        self._http = http
        self.audited = audited
        self.chunk = max(256 * 1024, chunk - chunk % (256 * 1024))

    # ---- תשתית ----
    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), trust_env=True)
        return self._http

    @staticmethod
    def _creds() -> tuple[str, str]:
        return SECRETS.get("youtube_client_id") or "", SECRETS.get("youtube_client_secret") or ""

    def configured(self) -> bool:
        cid, secret = self._creds()
        return bool(cid and secret)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            formats=("short", "long"),
            # Shorts עד 3 דקות; ערוץ לא מאומת מוגבל ל-15 דקות, מאומת – עד 12 שעות
            max_seconds={"short": 180.0, "long": 12 * 3600.0},
            max_bytes=256 * 1024 ** 3, title_max=100, description_max=5000, tags_max=30,
            privacy=("private", "unlisted", "public") if not self.audited
            else ("public", "unlisted", "private"),
            native_scheduling=True, thumbnail=True, public_requires_audit=True,
            notes=("shorts_auto", "synthetic_media", "made_for_kids"))

    def warnings(self, req: PublishRequest) -> list[dict[str, Any]]:
        out = []
        if not self.audited and req.privacy != "private":
            out.append({"key": "youtube_private_until_audit"})
        if req.format == "long" and req.duration > 15 * 60:
            out.append({"key": "youtube_long_needs_verified", "params": {"minutes": 15}})
        return out

    def validate(self, req: PublishRequest) -> list[dict[str, Any]]:
        issues = []
        if req.format == "short" and req.width and req.height and req.width > req.height:
            issues.append({"key": "youtube_short_not_vertical"})
        if any(c in req.title for c in "<>"):
            issues.append({"key": "youtube_title_brackets"})
        if sum(len(t) + 1 for t in req.tags) > 500:
            issues.append({"key": "youtube_tags_too_long", "params": {"max": 500}})
        return issues

    # ---- OAuth ----
    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        cid, _ = self._creds()
        return AUTH_URL + "?" + urlencode({
            "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "true", "state": state,
            "code_challenge": code_challenge, "code_challenge_method": "S256"})

    def _token_request(self, data: dict[str, str]) -> TokenSet:
        cid, secret = self._creds()
        try:
            r = self._client().post(TOKEN_URL, data={**data, "client_id": cid, "client_secret": secret})
        except Exception as exc:                       # noqa: BLE001
            raise PublishError("retry", "publishing.error.temporary", detail=type(exc).__name__)
        if r.status_code >= 500:
            raise PublishError("retry", "publishing.error.temporary", detail=f"token {r.status_code}")
        if r.status_code != 200:
            # invalid_grant = הרשאה בוטלה/פגה → צריך להתחבר מחדש
            raise PublishError("auth", "publishing.error.reconnect",
                               detail=f"token {r.status_code} {_reason(r)}")
        body = r.json()
        exp = _now() + timedelta(seconds=int(body.get("expires_in") or 3600))
        return TokenSet(access_token=body["access_token"],
                        refresh_token=body.get("refresh_token") or data.get("refresh_token", ""),
                        expires_at=exp, scopes=tuple(str(body.get("scope") or "").split()))

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        t = self._token_request({"code": code, "redirect_uri": redirect_uri,
                                 "grant_type": "authorization_code", "code_verifier": code_verifier})
        if not t.refresh_token:
            # בלי refresh token החיבור יפוג תוך שעה – עדיף להיכשל עכשיו בהודעה ברורה
            raise PublishError("permanent", "publishing.error.oauth_failed", detail="no refresh_token")
        missing = [s for s in ("https://www.googleapis.com/auth/youtube.upload",) if t.scopes and s not in t.scopes]
        if missing:
            raise PublishError("permanent", "publishing.error.scope_missing",
                               params={"platform": self.name}, detail="upload scope not granted")
        return t

    def refresh(self, refresh_token: str) -> TokenSet:
        return self._token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"})

    def revoke(self, token: str) -> None:
        try:
            self._client().post(REVOKE_URL, data={"token": token})
        except Exception as exc:                       # noqa: BLE001
            log.info("youtube revoke failed: %s", type(exc).__name__)

    def account_info(self, tokens: TokenSet) -> AccountInfo:
        r = self._client().get(CHANNELS_URL, params={"part": "snippet", "mine": "true"},
                               headers={"Authorization": f"Bearer {tokens.access_token}"})
        if r.status_code == 401:
            raise PublishError("auth", "publishing.error.reconnect")
        if r.status_code != 200:
            raise PublishError("permanent", "publishing.error.oauth_failed", detail=f"channels {r.status_code}")
        items = r.json().get("items") or []
        if not items:
            raise PublishError("permanent", "publishing.error.youtube_no_channel")
        ch = items[0]
        sn = ch.get("snippet") or {}
        return AccountInfo(external_id=str(ch.get("id")), display_name=sn.get("title") or "",
                           handle=sn.get("customUrl") or "",
                           avatar_url=((sn.get("thumbnails") or {}).get("default") or {}).get("url", ""))

    # ---- העלאה ----
    def _metadata(self, req: PublishRequest) -> dict[str, Any]:
        status: dict[str, Any] = {
            "privacyStatus": req.privacy,
            "selfDeclaredMadeForKids": bool(req.options.get("made_for_kids", False)),
            "containsSyntheticMedia": bool(req.options.get("synthetic_media", False)),
        }
        if req.publish_at is not None:
            status["privacyStatus"] = "private"          # תנאי של YouTube ל-publishAt
            status["publishAt"] = _rfc3339(req.publish_at)
        snippet: dict[str, Any] = {"title": req.title, "description": req.description,
                                   "categoryId": str(req.options.get("category_id") or "22")}
        if req.tags:
            snippet["tags"] = req.tags
        lang = str(req.options.get("language") or "")
        if lang:
            snippet["defaultLanguage"] = lang
            snippet["defaultAudioLanguage"] = lang
        return {"snippet": snippet, "status": status}

    def _check(self, r: Any, stage: str) -> None:
        """שגיאת HTTP → סוג הטיפול (ניסיון חוזר / חיבור מחדש / קבוע)."""
        code = r.status_code
        reason = _reason(r)
        if code == 401:
            raise PublishError("auth", "publishing.error.reconnect", detail=f"{stage} 401")
        if code == 403 and reason in ("quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded",
                                      "userRateLimitExceeded", "dailyLimitExceeded"):
            raise PublishError("permanent", "publishing.error.quota", detail=f"{stage} {reason}")
        if code == 403 and reason in ("forbidden", "insufficientPermissions"):
            raise PublishError("auth", "publishing.error.reconnect", detail=f"{stage} {reason}")
        if code in (429,) or code >= 500:
            raise PublishError("retry", "publishing.error.temporary", detail=f"{stage} {code}")
        raise PublishError("permanent", "publishing.error.rejected",
                           params={"reason": reason or str(code)}, detail=f"{stage} {code}")

    def publish(self, tokens: TokenSet, req: PublishRequest, *,
                on_progress: ProgressFn = None) -> PublishResult:
        path = Path(req.media_path)
        size = path.stat().st_size
        auth = {"Authorization": f"Bearer {tokens.access_token}"}
        http = self._client()
        try:
            r = http.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                          headers={**auth, "Content-Type": "application/json; charset=UTF-8",
                                   "X-Upload-Content-Type": "video/mp4",
                                   "X-Upload-Content-Length": str(size)},
                          content=json.dumps(self._metadata(req)).encode("utf-8"))
        except Exception as exc:                       # noqa: BLE001
            raise PublishError("retry", "publishing.error.temporary", detail=type(exc).__name__)
        if r.status_code != 200 or not r.headers.get("location"):
            self._check(r, "init")
        session = r.headers["location"]
        video = self._upload(session, path, size, auth, on_progress)
        vid = str(video.get("id") or "")
        if not vid:
            raise PublishError("retry", "publishing.error.temporary", detail="no video id")
        if req.format == "long" and req.options.get("thumbnail_path"):
            self._thumbnail(vid, Path(req.options["thumbnail_path"]), auth)
        url = f"https://www.youtube.com/shorts/{vid}" if req.format == "short" \
            else f"https://www.youtube.com/watch?v={vid}"
        state = "scheduled_on_platform" if req.publish_at is not None else "published"
        return PublishResult(remote_id=vid, url=url, state=state)

    def _upload(self, session: str, path: Path, size: int, auth: dict[str, str],
                on_progress: ProgressFn) -> dict[str, Any]:
        http = self._client()
        offset, resumes, err = 0, 0, ""
        with path.open("rb") as f:
            while True:
                f.seek(offset)
                data = f.read(self.chunk)
                end = offset + len(data) - 1
                headers = {**auth, "Content-Type": "video/mp4",
                           "Content-Range": f"bytes {offset}-{end}/{size}" if data
                           else f"bytes */{size}"}
                try:
                    r = http.put(session, headers=headers, content=data)
                except Exception as exc:               # noqa: BLE001
                    r = None
                    err = type(exc).__name__
                if r is not None and r.status_code in (200, 201):
                    if on_progress:
                        on_progress(1.0)
                    return r.json()
                if r is not None and r.status_code == 308:
                    offset = self._received(r)
                    if on_progress and size:
                        on_progress(min(0.99, offset / size))
                    continue
                if r is not None and r.status_code not in (500, 502, 503, 504, 429):
                    self._check(r, "upload")
                # תקלה זמנית באמצע: שואלים כמה התקבל וממשיכים משם
                resumes += 1
                if resumes > MAX_RESUMES:
                    raise PublishError("retry", "publishing.error.temporary",
                                       detail=f"upload interrupted ({err if r is None else r.status_code})")
                offset = self._query(session, size, auth)

    @staticmethod
    def _received(r: Any) -> int:
        rng = r.headers.get("range") or ""          # "bytes=0-1234"
        try:
            return int(rng.split("-")[-1]) + 1 if rng else 0
        except ValueError:
            return 0

    def _query(self, session: str, size: int, auth: dict[str, str]) -> int:
        try:
            r = self._client().put(session, headers={**auth, "Content-Range": f"bytes */{size}"},
                                   content=b"")
        except Exception:                               # noqa: BLE001
            return 0
        if r.status_code == 308:
            return self._received(r)
        if r.status_code == 404:                         # ה-session פג – מתחילים מחדש בניסיון הבא
            raise PublishError("retry", "publishing.error.temporary", detail="session expired")
        return 0

    def _thumbnail(self, vid: str, thumb: Path, auth: dict[str, str]) -> None:
        """רק לסרטון ארוך; כשל לא מכשיל את הפרסום (למשל ערוץ לא מאומת)."""
        try:
            if not thumb.exists() or thumb.stat().st_size > THUMB_MAX_BYTES:
                return
            mime = "image/png" if thumb.suffix.lower() == ".png" else "image/jpeg"
            r = self._client().post(THUMB_URL, params={"videoId": vid},
                                    headers={**auth, "Content-Type": mime}, content=thumb.read_bytes())
            if r.status_code != 200:
                log.info("youtube thumbnail not set: %s %s", r.status_code, _reason(r))
        except Exception as exc:                        # noqa: BLE001
            log.info("youtube thumbnail not set: %s", type(exc).__name__)
