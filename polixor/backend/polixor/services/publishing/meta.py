"""
Meta: עמודי Facebook וחשבונות Instagram מקצועיים – חיבור אחד משותף.

תיעוד רשמי שנבדק ותאריך הבדיקה: docs/providers/meta.md. עיקרי הדברים:

  * Facebook Login (Graph API v26.0) עם PKCE (S256). הרשאות מינימליות:
    pages_show_list, pages_read_engagement, pages_manage_posts (פרסום
    וידאו בעמוד), instagram_basic, instagram_content_publish.
  * טוקן משתמש קצר → טוקן משתמש ארוך (~60 יום, fb_exchange_token) →
    /me/accounts: טוקן עמוד לכל עמוד. טוקן עמוד שנגזר מטוקן ארוך לא פג,
    אבל יכול להתבטל (סיסמה שהוחלפה, הרשאה שהוסרה) → שגיאה 190 → חיבור מחדש.
  * חשבון Instagram מקצועי מקושר לעמוד (instagram_business_account) –
    מתגלה אוטומטית ומפורסם דרך graph.facebook.com עם טוקן העמוד.
  * Instagram Reels: container (media_type=REELS, upload_type=resumable) →
    העלאה ל-rupload → המתנה ל-status_code=FINISHED → media_publish.
    אין תזמון ב-API → Polixor מתזמן. מגבלה: 100 פרסומים ב-24 שעות.
    כריכה: thumb_offset (פריים מהסרטון); cover_url דורש כתובת ציבורית ולכן
    לא מוצע.
  * Facebook Reels: /{page}/video_reels start → rupload → finish
    (PUBLISHED / SCHEDULED + scheduled_publish_time, 10 דקות–29 ימים).
    3–90 שניות, אנכי, לפחות 540x960. מגבלה: 30 ב-24 שעות.
  * Facebook וידאו ארוך: Resumable Upload API (/{app-id}/uploads עם טוקן
    המשתמש, file_offset) → handle → /{page}/videos עם
    fbuploader_video_file_chunk; תזמון 10 דקות–6 חודשים; תמונה ממוזערת
    ב-/{video}/thumbnails. graph-video.facebook.com הוצא משימוש.

אין פרסום כפול: אחרי כל שלב נשמרת נקודת ביניים (container / video_id /
upload session / handle). ניסיון חוזר ממשיך ממנה, ולפני כל פרסום בודקים
אם הוא כבר קרה (container ב-PUBLISHED, video_id שכבר נוצר).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from ...config import SECRETS
from .base import (AccountInfo, Capabilities, PublishError, PublishRequest, PublishResult,
                   Provider, ProgressFn, TokenSet)

log = logging.getLogger("polixor.publishing.meta")

GRAPH_VERSION = "v26.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
SCOPES = ("pages_show_list", "pages_read_engagement", "pages_manage_posts",
          "instagram_basic", "instagram_content_publish")
IG_LIMIT = 100
FB_REEL_MIN, FB_REEL_MAX = 3.0, 90.0
FB_REEL_SCHEDULE = (timedelta(minutes=10), timedelta(days=29))
FB_VIDEO_SCHEDULE = (timedelta(minutes=10), timedelta(days=180))
IG_MAX_SECONDS = 15 * 60
IG_MAX_BYTES = 300 * 1024 * 1024
CAPTION_MAX = 2200
HASHTAGS_MAX = 30
THUMB_MAX_BYTES = 10 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def caption(req: PublishRequest) -> str:
    """כותרת + תיאור + תגיות כהאשטגים (Instagram ו-Facebook לא מפרידים כותרת)."""
    tags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in req.tags if t.strip())
    parts = [p for p in (req.title.strip(), req.description.strip(), tags) if p]
    return "\n\n".join(parts)


class GraphError(Exception):
    pass


class MetaClient:
    """קריאות Graph עם מיפוי שגיאות אחיד. בלי טוקנים ביומן ובהודעות."""

    def __init__(self, http: Any = None) -> None:
        self._http = http

    def http(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0), trust_env=True)
        return self._http

    @staticmethod
    def creds() -> tuple[str, str]:
        return SECRETS.get("meta_app_id") or "", SECRETS.get("meta_app_secret") or ""

    @staticmethod
    def error(r: Any, stage: str) -> PublishError:
        code = r.status_code
        try:
            err = (r.json() or {}).get("error") or {}
        except Exception:                               # noqa: BLE001
            err = {}
        ec, sub = int(err.get("code") or 0), int(err.get("error_subcode") or 0)
        detail = f"{stage} http={code} code={ec} sub={sub}"
        if ec in (190, 102) or code == 401:
            return PublishError("auth", "publishing.error.reconnect", detail=detail)
        if ec == 10 or 200 <= ec <= 299:
            return PublishError("auth", "publishing.error.scope_missing",
                                params={"platform": "Meta"}, detail=detail)
        # מגבלת פרסום יומית של Instagram / Reels
        if sub in (2207042,) or ec == 9:
            return PublishError("permanent", "publishing.error.quota", detail=detail)
        if ec in (4, 17, 32, 613) or 80001 <= ec <= 80014:
            return PublishError("retry", "publishing.error.temporary", detail=detail)
        if err.get("is_transient") or ec in (1, 2) or code >= 500 or code == 429:
            return PublishError("retry", "publishing.error.temporary", detail=detail)
        return PublishError("permanent", "publishing.error.rejected",
                            params={"reason": f"{ec}/{sub}" if ec else str(code)}, detail=detail)

    def get(self, path: str, token: str, stage: str, **params: Any) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
        try:
            q = {**params, **({"access_token": token} if token else {})}
            if "?" in url:                               # קישור "הבא" מ-paging – שומרים את הפרמטרים שלו
                from urllib.parse import parse_qsl, urlsplit

                parts = urlsplit(url)
                q = {**dict(parse_qsl(parts.query)), **q}
                url = parts._replace(query="").geturl()
            r = self.http().get(url, params=q)
        except Exception as exc:                        # noqa: BLE001
            raise PublishError("retry", "publishing.error.temporary", detail=f"{stage} {type(exc).__name__}")
        if r.status_code != 200:
            raise self.error(r, stage)
        return r.json()

    def post(self, path: str, token: str, stage: str, *, files: Any = None,
             **data: Any) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{GRAPH}/{path.lstrip('/')}"
        try:
            r = self.http().post(url, data={**{k: v for k, v in data.items() if v is not None},
                                            "access_token": token}, files=files)
        except Exception as exc:                        # noqa: BLE001
            raise PublishError("retry", "publishing.error.temporary", detail=f"{stage} {type(exc).__name__}")
        if r.status_code != 200:
            raise self.error(r, stage)
        return r.json()

    def upload_bytes(self, url: str, token: str, path: Path, *, offset_header: str,
                     offset: int, stage: str) -> dict[str, Any]:
        """העלאת הקובץ (מ-offset) לנקודת rupload / upload session."""
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()
        headers = {"Authorization": f"OAuth {token}", offset_header: str(offset),
                   "file_size": str(size), "Content-Type": "application/octet-stream"}
        try:
            r = self.http().post(url, headers=headers, content=data)
        except Exception as exc:                        # noqa: BLE001
            raise PublishError("retry", "publishing.error.temporary", detail=f"{stage} {type(exc).__name__}")
        if r.status_code != 200:
            raise self.error(r, stage)
        return r.json()

    # ---- OAuth משותף ----
    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        app_id, _ = self.creds()
        return DIALOG + "?" + urlencode({
            "client_id": app_id, "redirect_uri": redirect_uri, "state": state,
            "response_type": "code", "scope": ",".join(SCOPES),
            "code_challenge": code_challenge, "code_challenge_method": "S256"})

    def exchange(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        app_id, secret = self.creds()
        short = self.get("oauth/access_token", "", "token", client_id=app_id, client_secret=secret,
                         redirect_uri=redirect_uri, code=code, code_verifier=code_verifier)
        long = self.get("oauth/access_token", "", "long_token", grant_type="fb_exchange_token",
                        client_id=app_id, client_secret=secret,
                        fb_exchange_token=short["access_token"])
        exp = _now() + timedelta(seconds=int(long.get("expires_in") or 60 * 24 * 3600))
        granted = self.get("me/permissions", long["access_token"], "permissions")
        ok = {p["permission"] for p in granted.get("data") or [] if p.get("status") == "granted"}
        return TokenSet(access_token=long["access_token"], refresh_token=long["access_token"],
                        expires_at=exp, refresh_expires_at=exp, scopes=tuple(sorted(ok)))

    def discover(self, user: TokenSet) -> list[tuple[str, AccountInfo, TokenSet]]:
        """כל עמוד שאפשר לפרסם בו, וחשבון ה-Instagram המקצועי שמקושר אליו."""
        out: list[tuple[str, AccountInfo, TokenSet]] = []
        fields = ("id,name,access_token,tasks,picture{url},"
                  "instagram_business_account{id,username,name,profile_picture_url}")
        page = self.get("me/accounts", user.access_token, "accounts", fields=fields, limit=50)
        pages: list[dict[str, Any]] = list(page.get("data") or [])
        for _ in range(4):                               # עד 5 עמודי תוצאות
            nxt = (page.get("paging") or {}).get("next")
            if not nxt:
                break
            page = self.get(nxt, user.access_token, "accounts_next")
            pages += list(page.get("data") or [])
        scopes = set(user.scopes)
        for pg in pages:
            tasks = set(pg.get("tasks") or [])
            if tasks and "CREATE_CONTENT" not in tasks:
                continue                                  # אין הרשאת פרסום בעמוד הזה
            ptok = TokenSet(access_token=pg["access_token"], refresh_token=user.access_token,
                            expires_at=None, refresh_expires_at=user.refresh_expires_at,
                            scopes=user.scopes)
            pic = ((pg.get("picture") or {}).get("data") or {}).get("url", "")
            if "pages_manage_posts" in scopes or not scopes:
                out.append(("facebook", AccountInfo(external_id=str(pg["id"]), display_name=pg.get("name", ""),
                                                    avatar_url=pic, meta={"kind": "page"}), ptok))
            ig = pg.get("instagram_business_account") or {}
            if ig.get("id") and ("instagram_content_publish" in scopes or not scopes):
                out.append(("instagram", AccountInfo(
                    external_id=str(ig["id"]), display_name=ig.get("name") or ig.get("username", ""),
                    handle="@" + ig["username"] if ig.get("username") else "",
                    avatar_url=ig.get("profile_picture_url", ""),
                    meta={"kind": "instagram", "page_id": str(pg["id"]), "page_name": pg.get("name", "")}),
                    ptok))
        return out

    def revoke(self, user_token: str) -> None:
        try:
            self.http().delete(f"{GRAPH}/me/permissions", params={"access_token": user_token})
        except Exception as exc:                        # noqa: BLE001
            log.info("meta revoke failed: %s", type(exc).__name__)


class _MetaBase(Provider):
    credential_group = "meta"
    uses_pkce = True
    scopes = SCOPES

    def __init__(self, *, http: Any = None, client: Optional[MetaClient] = None) -> None:
        self.meta = client or MetaClient(http)

    def configured(self) -> bool:
        a, b = MetaClient.creds()
        return bool(a and b)

    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        return self.meta.authorize_url(state=state, redirect_uri=redirect_uri,
                                       code_challenge=code_challenge)

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        t = self.meta.exchange(code=code, redirect_uri=redirect_uri, code_verifier=code_verifier)
        if "pages_show_list" not in t.scopes:
            raise PublishError("permanent", "publishing.error.scope_missing",
                               params={"platform": "Meta"}, detail="pages_show_list not granted")
        return t

    def accounts(self, tokens: TokenSet) -> list[tuple[str, AccountInfo, TokenSet]]:
        return self.meta.discover(tokens)

    def refresh(self, refresh_token: str) -> TokenSet:
        # טוקן עמוד לא פג; טוקן משתמש (60 יום) לא מתחדש בלי כניסה מחדש
        raise PublishError("auth", "publishing.error.reconnect")

    def revoke(self, token: str) -> None:
        self.meta.revoke(token)

    @staticmethod
    def _caption_issues(req: PublishRequest) -> list[dict[str, Any]]:
        issues = []
        text = caption(req)
        if len(text) > CAPTION_MAX:
            issues.append({"key": "meta_caption_too_long", "params": {"max": CAPTION_MAX}})
        if text.count("#") > HASHTAGS_MAX:
            issues.append({"key": "meta_too_many_hashtags", "params": {"max": HASHTAGS_MAX}})
        return issues


# --------------------------------------------------------------------------
class InstagramProvider(_MetaBase):
    id = "instagram"
    name = "Instagram"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            formats=("short", "long"),        # שניהם מתפרסמים כ-Reel (עד 15 דקות)
            max_seconds={"short": float(IG_MAX_SECONDS), "long": float(IG_MAX_SECONDS)},
            max_bytes=IG_MAX_BYTES, title_max=CAPTION_MAX, description_max=CAPTION_MAX,
            tags_max=HASHTAGS_MAX, privacy=("public",), native_scheduling=False,
            thumbnail=False, notes=("cover_frame", "caption_combined", "share_to_feed"))

    def validate(self, req: PublishRequest) -> list[dict[str, Any]]:
        issues = self._caption_issues(req)
        if req.duration < 3:
            issues.append({"key": "meta_too_short", "params": {"seconds": 3}})
        off = req.options.get("cover_frame_seconds")
        if off is not None and not (0 <= float(off) <= req.duration):
            issues.append({"key": "meta_cover_out_of_range"})
        return issues

    def warnings(self, req: PublishRequest) -> list[dict[str, Any]]:
        if req.width and req.height and req.width > req.height:
            return [{"key": "instagram_horizontal"}]
        return []

    def _limit_reached(self, ig: str, token: str) -> bool:
        try:
            d = self.meta.get(f"{ig}/content_publishing_limit", token, "limit",
                              fields="quota_usage,config")
        except PublishError:
            return False                                  # לא חוסמים בגלל בדיקה שנכשלה
        row = (d.get("data") or [{}])[0]
        total = int(((row.get("config") or {}).get("quota_total")) or IG_LIMIT)
        return int(row.get("quota_usage") or 0) >= total

    def publish(self, tokens: TokenSet, req: PublishRequest, *,
                on_progress: ProgressFn = None) -> PublishResult:
        ig = str(req.account.get("external_id") or "")
        tok = tokens.access_token
        cp = req.checkpoint
        if cp.get("media_id"):                            # כבר פורסם – לא שוב
            return PublishResult(remote_id=cp["media_id"], url=cp.get("url", ""), state="published")
        if not cp.get("container"):
            if self._limit_reached(ig, tok):
                raise PublishError("permanent", "publishing.error.quota")
            off = req.options.get("cover_frame_seconds")
            d = self.meta.post(f"{ig}/media", tok, "container", media_type="REELS",
                               upload_type="resumable", caption=caption(req),
                               share_to_feed="true" if req.options.get("share_to_feed", True) else "false",
                               thumb_offset=str(int(float(off) * 1000)) if off is not None else None)
            req.save(container=str(d["id"]), uri=str(d.get("uri") or ""), uploaded=False)
        if not cp.get("uploaded"):
            uri = cp.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}/{cp['container']}"
            self.meta.upload_bytes(uri, tok, Path(req.media_path), offset_header="offset",
                                   offset=0, stage="ig_upload")
            req.save(uploaded=True)
            if on_progress:
                on_progress(0.9)
        st = self.meta.get(cp["container"], tok, "container_status", fields="status_code,status")
        code = str(st.get("status_code") or "")
        if code == "IN_PROGRESS" or not code:
            return PublishResult(remote_id=cp["container"], state="processing")
        if code == "EXPIRED":
            req.save(container="", uri="", uploaded=False)   # container חדש בניסיון הבא
            raise PublishError("retry", "publishing.error.temporary", detail="container expired")
        if code == "ERROR":
            req.save(container="", uri="", uploaded=False)
            raise PublishError("permanent", "publishing.error.rejected",
                               params={"reason": str(st.get("status") or "ERROR")[:80]})
        if code == "PUBLISHED":
            media_id = self._find_published(ig, tok, caption(req))
        else:                                             # FINISHED
            req.save(publishing=True)
            d = self.meta.post(f"{ig}/media_publish", tok, "publish", creation_id=cp["container"])
            media_id = str(d["id"])
        url = ""
        if media_id:
            try:
                url = str(self.meta.get(media_id, tok, "permalink", fields="permalink").get("permalink") or "")
            except PublishError:
                url = ""
        req.save(media_id=media_id or cp["container"], url=url)
        return PublishResult(remote_id=media_id or cp["container"], url=url, state="published")

    def _find_published(self, ig: str, tok: str, text: str) -> str:
        """ה-container כבר פורסם (למשל התשובה לא הגיעה): מוצאים את הפוסט במקום לפרסם שוב."""
        d = self.meta.get(f"{ig}/media", tok, "recent", fields="id,caption,timestamp", limit=10)
        for m in d.get("data") or []:
            if (m.get("caption") or "").strip() == text.strip():
                return str(m["id"])
        return ""


# --------------------------------------------------------------------------
class FacebookProvider(_MetaBase):
    id = "facebook"
    name = "Facebook"

    def capabilities(self) -> Capabilities:
        return Capabilities(
            formats=("short", "long"),       # short = Reel (3–90 שניות); long = וידאו בעמוד
            max_seconds={"short": FB_REEL_MAX, "long": 4 * 3600.0},
            max_bytes=10 * 1024 ** 3, title_max=255, description_max=5000, tags_max=30,
            privacy=("public",), native_scheduling=True, thumbnail=True,
            notes=("reel_3_90s", "thumbnail_long"))

    def validate(self, req: PublishRequest) -> list[dict[str, Any]]:
        issues = self._caption_issues(req)
        if req.format == "short":
            if req.duration < FB_REEL_MIN:
                issues.append({"key": "meta_too_short", "params": {"seconds": int(FB_REEL_MIN)}})
            if req.width and req.height and req.width > req.height:
                issues.append({"key": "facebook_reel_not_vertical"})
            if req.width and req.height and min(req.width, req.height) < 540:
                issues.append({"key": "facebook_reel_resolution", "params": {"w": 540, "h": 960}})
        when = _aware(req.publish_at)
        if when is not None:
            lo, hi = FB_REEL_SCHEDULE if req.format == "short" else FB_VIDEO_SCHEDULE
            if when < _now() + lo:
                issues.append({"key": "facebook_schedule_too_soon", "params": {"minutes": 10}})
            elif when > _now() + hi:
                issues.append({"key": "schedule_too_far", "params": {"days": hi.days}})
        return issues

    def publish(self, tokens: TokenSet, req: PublishRequest, *,
                on_progress: ProgressFn = None) -> PublishResult:
        if req.format == "short":
            return self._reel(tokens, req, on_progress)
        return self._video(tokens, req, on_progress)

    # ---- Reel ----
    def _reel(self, tokens: TokenSet, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
        page = str(req.account.get("external_id") or "")
        tok = tokens.access_token
        cp = req.checkpoint
        if not cp.get("video_id"):
            d = self.meta.post(f"{page}/video_reels", tok, "reel_start", upload_phase="start")
            req.save(video_id=str(d["video_id"]), upload_url=str(d.get("upload_url") or ""),
                     uploaded=False, finished=False)
        vid = cp["video_id"]
        if not cp.get("uploaded"):
            url = cp.get("upload_url") or f"https://rupload.facebook.com/video-upload/{GRAPH_VERSION}/{vid}"
            self.meta.upload_bytes(url, tok, Path(req.media_path), offset_header="offset",
                                   offset=0, stage="reel_upload")
            req.save(uploaded=True)
            if on_progress:
                on_progress(0.9)
        when = _aware(req.publish_at)
        if not cp.get("finished"):
            self.meta.post(f"{page}/video_reels", tok, "reel_finish", upload_phase="finish",
                           video_id=vid, video_state="SCHEDULED" if when else "PUBLISHED",
                           scheduled_publish_time=str(int(when.timestamp())) if when else None,
                           description=caption(req), title=req.title or None)
            req.save(finished=True)
        return self._status(vid, tok, when, f"https://www.facebook.com/reel/{vid}")

    # ---- וידאו ארוך ----
    def _video(self, tokens: TokenSet, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
        page = str(req.account.get("external_id") or "")
        tok = tokens.access_token
        user = tokens.refresh_token                      # טוקן המשתמש (60 יום) – להעלאה
        cp = req.checkpoint
        path = Path(req.media_path)
        size = path.stat().st_size
        when = _aware(req.publish_at)
        if cp.get("video_id"):                           # כבר נוצר בעמוד – לא שוב
            return self._status(cp["video_id"], tok, when, cp.get("url", ""))
        if not user:
            raise PublishError("auth", "publishing.error.reconnect", detail="no user token")
        app_id, _ = MetaClient.creds()
        if not cp.get("handle"):
            if not cp.get("session"):
                d = self.meta.post(f"{app_id}/uploads", user, "upload_start",
                                   file_name=path.name, file_length=str(size), file_type="video/mp4")
                req.save(session=str(d["id"]))
            # כמה כבר התקבל (חידוש אחרי תקלה)
            got = self.meta.get(cp["session"], user, "upload_offset")
            offset = int(got.get("file_offset") or 0)
            d = self.meta.upload_bytes(f"{GRAPH}/{cp['session']}", user, path,
                                       offset_header="file_offset", offset=offset, stage="upload")
            if not d.get("h"):
                raise PublishError("retry", "publishing.error.temporary", detail="no handle")
            req.save(handle=str(d["h"]))
            if on_progress:
                on_progress(0.9)
        tags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in req.tags if t.strip())
        d = self.meta.post(f"{page}/videos", tok, "video_publish",
                           fbuploader_video_file_chunk=cp["handle"], title=req.title or None,
                           description="\n\n".join(x for x in (req.description.strip(), tags) if x) or None,
                           published="false" if when else "true",
                           scheduled_publish_time=str(int(when.timestamp())) if when else None)
        vid = str(d["id"])
        req.save(video_id=vid, url=f"https://www.facebook.com/{page}/videos/{vid}")
        thumb = req.options.get("thumbnail_path")
        if thumb and not cp.get("thumb_done"):
            self._thumbnail(vid, tok, Path(thumb))
            req.save(thumb_done=True)
        return self._status(vid, tok, when, cp["url"])

    def _thumbnail(self, vid: str, tok: str, thumb: Path) -> None:
        """תמונה ממוזערת לסרטון ארוך – כשל לא מכשיל את הפרסום."""
        try:
            if not thumb.exists() or thumb.stat().st_size > THUMB_MAX_BYTES:
                return
            mime = "image/png" if thumb.suffix.lower() == ".png" else "image/jpeg"
            self.meta.post(f"{vid}/thumbnails", tok, "thumbnail", is_preferred="true",
                           files={"source": (thumb.name, thumb.read_bytes(), mime)})
        except PublishError as exc:
            log.info("facebook thumbnail not set: %s", exc.detail)

    def _status(self, vid: str, tok: str, when: Optional[datetime], url: str) -> PublishResult:
        st = self.meta.get(vid, tok, "video_status", fields="status")
        status = st.get("status") or {}
        video = str(status.get("video_status") or "")
        pub = str(((status.get("publishing_phase") or {}).get("status")) or "")
        if video == "error" or pub == "error":
            raise PublishError("permanent", "publishing.error.rejected",
                               params={"reason": "processing error"})
        if when is not None:
            # התזמון נקבע כבר ב-finish / בפרסום; Facebook יעבד ויפרסם בזמן שנקבע
            return PublishResult(remote_id=vid, url=url, state="scheduled_on_platform")
        if pub == "complete" or (video == "ready" and not pub):
            return PublishResult(remote_id=vid, url=url, state="published")
        return PublishResult(remote_id=vid, url=url, state="processing")
