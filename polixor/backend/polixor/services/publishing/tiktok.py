"""
TikTok (Content Posting API – Direct Post) עם Login Kit for Web.
תיעוד רשמי שנבדק ותאריך הבדיקה: docs/providers/tiktok.md. עיקרי הדברים:

  * OAuth: www.tiktok.com/v2/auth/authorize (client_key, scope מופרד בפסיקים),
    טוקן ב-open.tiktokapis.com/v2/oauth/token. PKCE (S256) – אבל
    ה-code_challenge של TikTok הוא SHA-256 ב-hex (לא base64url כמו בתקן).
    access token – 24 שעות; refresh token – כ-365 יום; ביטול ב-/oauth/revoke.
    הרשאות: user.info.basic, video.publish.
  * לפני כל פרסום (וגם בחלון הפרסום): creator_info/query – כינוי היוצר,
    אפשרויות הפרטיות, הגדרות תגובות/דואט/סטיץ' שהיוצר כיבה, ואורך מרבי.
    לפי ההנחיות: הכינוי מוצג, אין ברירת מחדל לפרטיות, האינטראקציות
    לא מסומנות מראש, גילוי תוכן מסחרי, ושורת אישור שימוש במוזיקה.
  * Direct Post עם FILE_UPLOAD: video/init (post_info + source_info) →
    upload_url → PUT בחלקים לפי הסדר (5–64MB; האחרון עד 128MB; קובץ מתחת
    ל-5MB – חלק אחד) → status/fetch עד PUBLISH_COMPLETE.
  * אפליקציה שלא עברה ביקורת (audit) – פרסום רק כ-SELF_ONLY, ולחשבון פרטי.
  * אין תזמון ב-API → Polixor מתזמן (השרת חייב לפעול).
  * מגבלות: 6 בקשות לדקה לטוקן, ומכסה יומית לחשבון (spam_risk_too_many_posts).

בלי פרסום כפול: publish_id ומספר החלקים שהועלו נשמרים אחרי כל חלק. ניסיון
חוזר ממשיך מהחלק הבא; publish_id שכל החלקים שלו הועלו לא מאותחל שוב –
רק בודקים את מצבו.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ...config import SECRETS
from .base import (AccountInfo, Capabilities, PublishError, PublishRequest, PublishResult,
                   Provider, ProgressFn, TokenSet)

log = logging.getLogger("polixor.publishing.tiktok")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPES = ("user.info.basic", "video.publish")
MIN_CHUNK = 5 * 1024 * 1024
CHUNK = 10 * 1024 * 1024          # בין 5 ל-64MB
CAPTION_MAX = 2200
PRIVACY = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")

_AUTH_CODES = {"access_token_invalid", "access_token_expired", "invalid_token", "token_expired"}
_SCOPE_CODES = {"scope_not_authorized", "scope_permission_missed"}
_RETRY_CODES = {"rate_limit_exceeded", "internal_error", "server_error"}
_QUOTA_CODES = {"spam_risk_too_many_posts", "spam_risk_user_banned_from_posting",
                "spam_risk_too_many_pending_share"}


def hex_challenge(verifier: str) -> str:
    """ה-code_challenge ש-TikTok מצפה לו: SHA-256 ב-hex."""
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


def chunk_plan(size: int, chunk: int = CHUNK) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) לפי כללי TikTok."""
    if size <= MIN_CHUNK:
        return size, 1
    chunk = max(MIN_CHUNK, min(64 * 1024 * 1024, chunk))
    return chunk, max(1, size // chunk)          # עיגול למטה; השארית נכנסת לחלק האחרון


def _caption(req: PublishRequest) -> str:
    tags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in req.tags if t.strip())
    parts = [p for p in (req.title.strip(), req.description.strip(), tags) if p]
    return "\n\n".join(parts)


class TikTokProvider(Provider):
    id = "tiktok"
    name = "TikTok"
    scopes = SCOPES
    uses_pkce = True
    pkce_challenge = staticmethod(hex_challenge)

    def __init__(self, *, http: Any = None, audited: bool = False, chunk: int = CHUNK) -> None:
        self._http = http
        self.audited = audited
        self.chunk = chunk

    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0), trust_env=True)
        return self._http

    @staticmethod
    def _creds() -> tuple[str, str]:
        return SECRETS.get("tiktok_client_key") or "", SECRETS.get("tiktok_client_secret") or ""

    def configured(self) -> bool:
        a, b = self._creds()
        return bool(a and b)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            formats=("short", "long"), max_seconds={"short": 600.0, "long": 600.0},
            max_bytes=4 * 1024 ** 3, title_max=CAPTION_MAX, description_max=CAPTION_MAX,
            tags_max=30, privacy=PRIVACY if self.audited else ("SELF_ONLY",),
            privacy_required=True, native_scheduling=False, thumbnail=False,
            public_requires_audit=True,
            notes=("cover_frame", "creator_info", "interactions", "commercial_disclosure",
                   "music_confirmation", "ai_label", "caption_combined"))

    # ---- שגיאות ----
    @staticmethod
    def _error(r: Any, stage: str) -> PublishError:
        try:
            err = (r.json() or {}).get("error") or {}
        except Exception:                               # noqa: BLE001
            err = {}
        code = str(err.get("code") or "")
        detail = f"{stage} http={r.status_code} code={code}"
        if r.status_code == 401 or code in _AUTH_CODES:
            return PublishError("auth", "publishing.error.reconnect", detail=detail)
        if code in _SCOPE_CODES:
            return PublishError("auth", "publishing.error.scope_missing",
                                params={"platform": "TikTok"}, detail=detail)
        if code in _QUOTA_CODES:
            return PublishError("permanent", "publishing.error.quota", detail=detail)
        if code == "unaudited_client_can_only_post_to_private_accounts":
            return PublishError("permanent", "publishing.error.tiktok_unaudited", detail=detail)
        if code == "privacy_level_option_mismatch":
            return PublishError("permanent", "publishing.error.tiktok_privacy", detail=detail)
        if code in _RETRY_CODES or r.status_code in (429,) or r.status_code >= 500:
            return PublishError("retry", "publishing.error.temporary", detail=detail)
        return PublishError("permanent", "publishing.error.rejected",
                            params={"reason": code or str(r.status_code)}, detail=detail)

    def _post(self, path: str, token: str, stage: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._client().post(f"{API}/{path}", json=body, headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
        except Exception as exc:                        # noqa: BLE001
            raise PublishError("retry", "publishing.error.temporary", detail=f"{stage} {type(exc).__name__}")
        data = r.json() if r.content else {}
        if r.status_code != 200 or ((data.get("error") or {}).get("code") not in (None, "", "ok")):
            raise self._error(r, stage)
        return data.get("data") or {}

    # ---- OAuth ----
    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        key, _ = self._creds()
        return AUTH_URL + "?" + urlencode({
            "client_key": key, "scope": ",".join(SCOPES), "response_type": "code",
            "redirect_uri": redirect_uri, "state": state,
            "code_challenge": code_challenge, "code_challenge_method": "S256"})

    def _token(self, data: dict[str, str]) -> TokenSet:
        key, secret = self._creds()
        try:
            r = self._client().post(f"{API}/oauth/token/", data={**data, "client_key": key,
                                                               "client_secret": secret},
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as exc:                        # noqa: BLE001
            raise PublishError("retry", "publishing.error.temporary", detail=type(exc).__name__)
        body = r.json() if r.content else {}
        if r.status_code >= 500:
            raise PublishError("retry", "publishing.error.temporary", detail=f"token {r.status_code}")
        if r.status_code != 200 or not body.get("access_token"):
            raise PublishError("auth", "publishing.error.reconnect",
                               detail=f"token {r.status_code} {str(body.get('error') or '')[:40]}")
        now = datetime.now(timezone.utc)
        return TokenSet(access_token=body["access_token"], refresh_token=body.get("refresh_token") or "",
                        expires_at=now + timedelta(seconds=int(body.get("expires_in") or 86400)),
                        refresh_expires_at=now + timedelta(seconds=int(body.get("refresh_expires_in") or 31536000)),
                        scopes=tuple(x for x in str(body.get("scope") or "").split(",") if x))

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        t = self._token({"code": code, "grant_type": "authorization_code",
                         "redirect_uri": redirect_uri, "code_verifier": code_verifier})
        if t.scopes and "video.publish" not in t.scopes:
            raise PublishError("permanent", "publishing.error.scope_missing",
                               params={"platform": self.name}, detail="video.publish not granted")
        return t

    def refresh(self, refresh_token: str) -> TokenSet:
        return self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def revoke(self, token: str) -> None:
        key, secret = self._creds()
        try:
            self._client().post(f"{API}/oauth/revoke/", data={"client_key": key, "client_secret": secret,
                                                             "token": token})
        except Exception as exc:                        # noqa: BLE001
            log.info("tiktok revoke failed: %s", type(exc).__name__)

    def account_info(self, tokens: TokenSet) -> AccountInfo:
        try:
            r = self._client().get(f"{API}/user/info/", params={"fields": "open_id,avatar_url,display_name"},
                                   headers={"Authorization": f"Bearer {tokens.access_token}"})
        except Exception as exc:                        # noqa: BLE001
            raise PublishError("retry", "publishing.error.temporary", detail=type(exc).__name__)
        if r.status_code != 200:
            raise self._error(r, "user_info")
        u = (r.json().get("data") or {}).get("user") or {}
        return AccountInfo(external_id=str(u.get("open_id") or ""), display_name=u.get("display_name") or "",
                           avatar_url=u.get("avatar_url") or "")

    # ---- פרטי היוצר (לפני כל פרסום, ובחלון הפרסום) ----
    def creator_info(self, tokens: TokenSet) -> dict[str, Any]:
        return self._post("post/publish/creator_info/query/", tokens.access_token, "creator_info", {})

    def account_details(self, tokens: TokenSet) -> dict[str, Any]:
        info = self.creator_info(tokens)
        opts = [o for o in (info.get("privacy_level_options") or []) if o in PRIVACY]
        if not self.audited:
            opts = [o for o in opts if o == "SELF_ONLY"] or ["SELF_ONLY"]
        return {"nickname": info.get("creator_nickname") or "",
                "username": info.get("creator_username") or "",
                "avatar_url": info.get("creator_avatar_url") or "",
                "privacy_options": opts,
                "comment_disabled": bool(info.get("comment_disabled")),
                "duet_disabled": bool(info.get("duet_disabled")),
                "stitch_disabled": bool(info.get("stitch_disabled")),
                "max_video_seconds": int(info.get("max_video_post_duration_sec") or 0),
                "audited": self.audited}

    def _creator_issues(self, info: dict[str, Any], req: PublishRequest) -> list[dict[str, Any]]:
        issues = []
        maxd = int(info.get("max_video_post_duration_sec") or 0)
        if maxd and req.duration > maxd + 0.5:
            issues.append({"key": "tiktok_too_long_for_creator", "params": {"seconds": maxd}})
        if req.privacy and req.privacy not in (info.get("privacy_level_options") or PRIVACY):
            issues.append({"key": "tiktok_privacy_not_offered"})
        o = req.options
        for flag, disabled in (("allow_comment", "comment_disabled"), ("allow_duet", "duet_disabled"),
                               ("allow_stitch", "stitch_disabled")):
            if o.get(flag) and info.get(disabled):
                issues.append({"key": "tiktok_interaction_disabled"})
                break
        return issues

    def live_checks(self, tokens: TokenSet, req: PublishRequest) -> list[dict[str, Any]]:
        return self._creator_issues(self.creator_info(tokens), req)

    def validate(self, req: PublishRequest) -> list[dict[str, Any]]:
        issues = []
        if len(_caption(req)) > CAPTION_MAX:
            issues.append({"key": "meta_caption_too_long", "params": {"max": CAPTION_MAX}})
        if req.duration < 3:
            issues.append({"key": "meta_too_short", "params": {"seconds": 3}})
        o = req.options
        if o.get("commercial"):
            if not (o.get("brand_organic") or o.get("brand_content")):
                issues.append({"key": "tiktok_commercial_choice"})
            if o.get("brand_content") and req.privacy == "SELF_ONLY":
                issues.append({"key": "tiktok_branded_not_private"})
        off = o.get("cover_frame_seconds")
        if off is not None and not (0 <= float(off) <= req.duration):
            issues.append({"key": "meta_cover_out_of_range"})
        return issues

    def warnings(self, req: PublishRequest) -> list[dict[str, Any]]:
        return [] if self.audited else [{"key": "tiktok_private_until_audit"}]

    # ---- פרסום ----
    def publish(self, tokens: TokenSet, req: PublishRequest, *,
                on_progress: ProgressFn = None) -> PublishResult:
        tok = tokens.access_token
        cp = req.checkpoint
        path = Path(req.media_path)
        size = path.stat().st_size
        if cp.get("post_id"):
            return PublishResult(remote_id=cp["publish_id"], url=cp.get("url", ""), state="published")
        if not cp.get("publish_id"):
            info = self.creator_info(tokens)                # תמיד מידע עדכני לפני פרסום
            issues = self._creator_issues(info, req) + self.validate(req)
            if issues:
                raise PublishError("permanent", "publishing.error.rejected",
                                   params={"reason": issues[0]["key"]})
            chunk, count = chunk_plan(size, self.chunk)
            o = req.options
            post_info: dict[str, Any] = {
                "title": _caption(req), "privacy_level": req.privacy,
                "disable_comment": not bool(o.get("allow_comment")),
                "disable_duet": not bool(o.get("allow_duet")),
                "disable_stitch": not bool(o.get("allow_stitch")),
                "brand_content_toggle": bool(o.get("commercial") and o.get("brand_content")),
                "brand_organic_toggle": bool(o.get("commercial") and o.get("brand_organic")),
                "is_aigc": bool(o.get("synthetic_media")),
            }
            if o.get("cover_frame_seconds") is not None:
                post_info["video_cover_timestamp_ms"] = int(float(o["cover_frame_seconds"]) * 1000)
            d = self._post("post/publish/video/init/", tok, "init", {
                "post_info": post_info,
                "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                "chunk_size": chunk, "total_chunk_count": count}})
            req.save(publish_id=str(d["publish_id"]), upload_url=str(d["upload_url"]),
                     chunk=chunk, count=count, sent=0, username=str(info.get("creator_username") or ""))
        if int(cp.get("sent", 0)) < int(cp["count"]):
            self._upload(req, path, size, on_progress)
        return self._status(req, tok)

    def _upload(self, req: PublishRequest, path: Path, size: int, on_progress: ProgressFn) -> None:
        cp = req.checkpoint
        chunk, count = int(cp["chunk"]), int(cp["count"])
        with path.open("rb") as f:
            for i in range(int(cp.get("sent", 0)), count):
                start = i * chunk
                end = size - 1 if i == count - 1 else start + chunk - 1
                f.seek(start)
                data = f.read(end - start + 1)
                try:
                    r = self._client().put(cp["upload_url"], content=data, headers={
                        "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{end}/{size}"})
                except Exception as exc:                # noqa: BLE001
                    raise PublishError("retry", "publishing.error.temporary",
                                       detail=f"chunk {i} {type(exc).__name__}")
                if r.status_code in (403, 404, 410):
                    # כתובת ההעלאה פגה (שעה) – ה-publish_id לא יפורסם; מתחילים מחדש
                    req.save(publish_id="", upload_url="", sent=0)
                    raise PublishError("retry", "publishing.error.temporary", detail=f"upload url {r.status_code}")
                if r.status_code not in (200, 201, 206):
                    raise self._error(r, f"chunk {i}")
                req.save(sent=i + 1)
                if on_progress:
                    on_progress((i + 1) / count * 0.9)

    def _status(self, req: PublishRequest, tok: str) -> PublishResult:
        cp = req.checkpoint
        d = self._post("post/publish/status/fetch/", tok, "status", {"publish_id": cp["publish_id"]})
        status = str(d.get("status") or "")
        if status == "FAILED":
            reason = str(d.get("fail_reason") or "failed")
            req.save(publish_id="", upload_url="", sent=0)       # ניסיון הבא – init חדש
            kind = "retry" if reason in ("internal", "server_error") else "permanent"
            raise PublishError(kind, "publishing.error.rejected", params={"reason": reason[:60]})
        if status != "PUBLISH_COMPLETE":
            return PublishResult(remote_id=cp["publish_id"], state="processing")
        ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
        user = cp.get("username", "")
        url = f"https://www.tiktok.com/@{user}/video/{ids[0]}" if ids and user else \
            (f"https://www.tiktok.com/@{user}" if user else "")
        req.save(post_id=str(ids[0]) if ids else cp["publish_id"], url=url)
        return PublishResult(remote_id=cp["publish_id"], url=url, state="published")
