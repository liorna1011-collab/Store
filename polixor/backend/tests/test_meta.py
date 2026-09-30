"""
בדיקות לספקי Meta – Instagram ו-Facebook (שלב 8) – מול שרת Graph מדומה
(httpx.MockTransport). אין בקשת רשת אמיתית ואין פרסום אמיתי.

  * OAuth משותף: כתובת (v26.0, PKCE S256, הרשאות מינימליות, בלי סוד), טוקן
    קצר → ארוך, בדיקת הרשאות שאושרו, גילוי עמודים (עם הרשאת פרסום בלבד)
    ו-Instagram מקושר, עמודי תוצאות, חיבור חלקי, טוקנים מוצפנים.
  * Instagram Reels: מגבלת 100, container (REELS/resumable/כיתוב/כריכה),
    rupload, "בעיבוד" → המתזמן בודק שוב → media_publish פעם אחת, permalink.
  * בלי כפילות: תשובת media_publish שאבדה → הניסיון הבא מוצא את הפוסט ולא
    מפרסם שוב; נפילה באמצע → ממשיכים מנקודת הביניים.
  * Facebook Reels: start/upload/finish, תזמון SCHEDULED, בדיקות 3–90 שניות,
    אנכי, 540x960, חלון תזמון.
  * Facebook וידאו ארוך: upload session בטוקן המשתמש, חידוש מ-file_offset,
    handle, פרסום/תזמון, תמונה ממוזערת, בלי יצירה כפולה.
  * שגיאות: 190 → חיבור מחדש + התראה; מגבלה יומית → קבוע; 4/transient → זמני.
  * ניתוק: ביטול ההרשאה רק כשזה החשבון האחרון שחולק את החיבור.

הרצה:  python3 tests/test_meta.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = tempfile.mkdtemp(prefix="pxmeta_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_SCHEDULER"] = "0"

import httpx                                                         # noqa: E402

from polixor.config import PATHS, SECRETS, SETTINGS                  # noqa: E402
from polixor.db import init_db, session_scope                        # noqa: E402
from polixor.models import (Clip, ClipKind, ClipStatus, Job, JobStatus,  # noqa: E402
                            Notification, PublishJob, SocialAccount, new_id)
from polixor.services.publishing import registry, service, vault    # noqa: E402
from polixor.services.publishing.base import PublishError, PublishRequest, TokenSet  # noqa: E402
from polixor.services.publishing.meta import (GRAPH, FacebookProvider, InstagramProvider,  # noqa: E402
                                              MetaClient, caption)

PATHS.ensure()
init_db()
APP_ID, APP_SECRET = "1234567890", "meta-secret-not-real"
SECRETS.set("meta_app_id", APP_ID)
SECRETS.set("meta_app_secret", APP_SECRET)
H = {"X-Polixor-Request": "1"}
V = "v26.0"


class FakeMeta:
    """Graph מדומה. כל בקשה נרשמת; כתובת לא מוכרת – כשל."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.granted = ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
                        "instagram_basic", "instagram_content_publish"]
        self.pages = [
            {"id": "P1", "name": "My Page", "access_token": "PAGE-TOKEN-1", "tasks": ["CREATE_CONTENT", "MANAGE"],
             "instagram_business_account": {"id": "IG1", "username": "mybrand", "name": "My Brand"}},
            {"id": "P2", "name": "Read only", "access_token": "PAGE-TOKEN-2", "tasks": ["ANALYZE"]},
        ]
        self.next_page: list = []
        self.quota = 0
        self.container_statuses = ["IN_PROGRESS", "FINISHED"]
        self.media_publish_calls = 0
        self.lose_publish_response = False
        self.published_captions: list[str] = []
        self.container_n = 0
        self.errors: dict[str, tuple[int, dict]] = {}     # path suffix → (status, error)
        self.reel_status = [{"video_status": "processing"},
                            {"video_status": "ready", "publishing_phase": {"status": "complete"}}]
        self.reel_finish: dict = {}
        self.session_received = 0
        self.fail_upload_once = False
        self.video_posts: list[dict] = []
        self.thumb_status = 200
        self.revoked = 0
        self.last_container: dict = {}

    def _err(self, key: str):
        if key in self.errors:
            code, err = self.errors.pop(key)
            return httpx.Response(code, json={"error": err})
        return None

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        url = str(req.url)
        path = urlsplit(url).path
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        form = {k: v[0] for k, v in parse_qs(req.content.decode(errors="ignore")).items()} \
            if req.headers.get("content-type", "").startswith("application/x-www-form-urlencoded") else {}
        if url.startswith(f"{GRAPH}/oauth/access_token"):
            if q.get("grant_type") == "fb_exchange_token":
                assert q["fb_exchange_token"] == "SHORT-USER"
                return httpx.Response(200, json={"access_token": "LONG-USER", "expires_in": 5183944})
            assert q["code_verifier"] and q["client_secret"] == APP_SECRET
            return httpx.Response(200, json={"access_token": "SHORT-USER", "expires_in": 3600})
        if path == f"/{V}/me/permissions":
            if req.method == "DELETE":
                self.revoked += 1
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"} for p in self.granted]})
        if path == f"/{V}/me/accounts":
            body = {"data": self.pages}
            if self.next_page:
                body["paging"] = {"next": f"{GRAPH}/me/accounts?after=X"}
                if q.get("after"):
                    body = {"data": self.next_page}
            return httpx.Response(200, json=body)
        # ---- Instagram ----
        if path == f"/{V}/IG1/content_publishing_limit":
            return httpx.Response(200, json={"data": [{"quota_usage": self.quota, "config": {"quota_total": 100}}]})
        if path == f"/{V}/IG1/media" and req.method == "POST":
            r = self._err("ig_container")
            if r:
                return r
            self.container_n += 1
            self.last_container = dict(form)
            cid = f"C{self.container_n}"
            return httpx.Response(200, json={"id": cid, "uri": f"https://rupload.facebook.com/ig-api-upload/{V}/{cid}"})
        if url.startswith("https://rupload.facebook.com/ig-api-upload/"):
            assert req.headers["authorization"].startswith("OAuth ") and req.headers["offset"] == "0"
            assert int(req.headers["file_size"]) == len(req.content)
            return httpx.Response(200, json={"success": True})
        if path.startswith(f"/{V}/C") and req.method == "GET":
            st = self.container_statuses.pop(0) if len(self.container_statuses) > 1 else self.container_statuses[0]
            return httpx.Response(200, json={"status_code": st})
        if path == f"/{V}/IG1/media_publish":
            self.media_publish_calls += 1
            self.published_captions.append(self.last_container.get("caption", ""))
            self.container_statuses = ["PUBLISHED"]
            if self.lose_publish_response:
                self.lose_publish_response = False
                raise httpx.ReadTimeout("response lost")
            return httpx.Response(200, json={"id": "M1"})
        if path == f"/{V}/IG1/media" and req.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "M1", "caption": c} for c in self.published_captions]})
        if path == f"/{V}/M1":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/abc/"})
        # ---- Facebook Reels ----
        if path == f"/{V}/P1/video_reels":
            r = self._err("reel_" + form.get("upload_phase", ""))
            if r:
                return r
            if form.get("upload_phase") == "start":
                return httpx.Response(200, json={"video_id": "R1",
                                                 "upload_url": f"https://rupload.facebook.com/video-upload/{V}/R1"})
            self.reel_finish = dict(form)
            return httpx.Response(200, json={"success": True})
        if url.startswith("https://rupload.facebook.com/video-upload/"):
            assert req.headers["authorization"] == "OAuth PAGE-TOKEN-1"
            return httpx.Response(200, json={"success": True})
        if path == f"/{V}/R1":
            st = self.reel_status.pop(0) if len(self.reel_status) > 1 else self.reel_status[0]
            return httpx.Response(200, json={"status": st})
        # ---- Facebook video (Resumable Upload API) ----
        if path == f"/{V}/{APP_ID}/uploads":
            assert form["access_token"] == "LONG-USER" and form["file_type"] == "video/mp4"
            return httpx.Response(200, json={"id": "upload:S1"})
        if path == f"/{V}/upload:S1":
            if req.method == "GET":
                return httpx.Response(200, json={"id": "upload:S1", "file_offset": self.session_received})
            off = int(req.headers["file_offset"])
            assert off == self.session_received, (off, self.session_received)
            assert req.headers["authorization"] == "OAuth LONG-USER"
            if self.fail_upload_once:
                self.fail_upload_once = False
                self.session_received = off + len(req.content) // 2
                return httpx.Response(503, json={"error": {"code": 2, "is_transient": True}})
            self.session_received = off + len(req.content)
            return httpx.Response(200, json={"h": "HANDLE-1"})
        if path == f"/{V}/P1/videos":
            self.video_posts.append(dict(form))
            return httpx.Response(200, json={"id": "V1"})
        if path == f"/{V}/V1/thumbnails":
            return httpx.Response(self.thumb_status, json={"success": self.thumb_status == 200})
        if path == f"/{V}/V1":
            return httpx.Response(200, json={"status": {"video_status": "ready"}})
        raise AssertionError(f"unexpected request {req.method} {url}")


def _client(m: FakeMeta) -> MetaClient:
    return MetaClient(http=httpx.Client(transport=httpx.MockTransport(m.handler)))


def _register(m: FakeMeta) -> None:
    c = _client(m)
    registry.register(InstagramProvider(client=c))
    registry.register(FacebookProvider(client=c))


def _unregister() -> None:
    registry.unregister("instagram")
    registry.unregister("facebook")


def _media(size: int = 200 * 1024, name: str = "m.mp4") -> Path:
    p = Path(DATA) / name
    p.write_bytes(os.urandom(size))
    return p


def _clip(kind=ClipKind.SHORT, duration=30.0, w=1080, h=1920, name="c.mp4") -> str:
    path = _media(name=name)
    thumb = Path(DATA) / f"{name}.jpg"
    thumb.write_bytes(b"\xff\xd8" + b"0" * 500)
    jid, cid = new_id(), new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="S", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[]))
        s.flush()
        s.add(Clip(id=cid, job_id=jid, kind=kind, status=ClipStatus.READY, title="Clip",
                   file_path=str(path), file_size=path.stat().st_size, duration=duration,
                   width=w, height=h, thumbnail_path=str(thumb)))
    return cid


def _api():
    from fastapi.testclient import TestClient

    from polixor.main import app
    return TestClient(app, base_url="http://testserver")


def _connect(c, platform: str = "facebook"):
    r = c.post(f"/api/publish/accounts/{platform}/connect", json={}, headers=H)
    assert r.status_code == 200, r.text
    q = parse_qs(urlsplit(r.json()["auth_url"]).query)
    return c.get(f"/api/publish/oauth/{platform}/callback?state={q['state'][0]}&code=CODE",
                 follow_redirects=False)


def _accounts(c) -> dict[str, dict]:
    return {a["platform"]: a for a in c.get("/api/publish/accounts").json()["accounts"]}


def _reset_accounts() -> None:
    with session_scope() as s:
        s.query(PublishJob).delete()
        s.query(SocialAccount).delete()


# --------------------------------------------------------------------------
def test_authorize_url_is_facebook_login_with_pkce_and_minimal_scopes():
    p = InstagramProvider(client=_client(FakeMeta()))
    assert p.configured() and p.credential_group == "meta"
    url = p.authorize_url(state="S", redirect_uri="https://me/cb", code_challenge="CH")
    q = parse_qs(urlsplit(url).query)
    assert url.startswith(f"https://www.facebook.com/{V}/dialog/oauth?")
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [APP_ID]
    assert set(q["scope"][0].split(",")) == {"pages_show_list", "pages_read_engagement", "pages_manage_posts",
                                             "instagram_basic", "instagram_content_publish"}
    assert APP_SECRET not in url


def test_one_facebook_login_connects_pages_and_linked_instagram():
    _reset_accounts()
    m = FakeMeta()
    _register(m)
    try:
        c = _api()
        r = _connect(c, "facebook")
        assert r.headers["location"].endswith("connected=facebook"), r.headers["location"]
        accs = _accounts(c)
        assert accs["facebook"]["display_name"] == "My Page"
        assert accs["instagram"]["handle"] == "@mybrand" and accs["instagram"]["linked_page"] == "My Page"
        body = c.get("/api/publish/accounts").text
        assert "PAGE-TOKEN" not in body and "LONG-USER" not in body
        with session_scope() as s:
            ids = {a.external_id for a in s.query(SocialAccount).all()}
            assert "P2" not in ids                          # עמוד בלי הרשאת פרסום
            a = s.query(SocialAccount).filter_by(platform="facebook").one()
            assert vault.decrypt(a.access_token_enc) == "PAGE-TOKEN-1"
            assert vault.decrypt(a.refresh_token_enc) == "LONG-USER"
            assert a.token_expires_at is None               # טוקן עמוד לא פג
        # חיבור שוב – מעדכן, לא משכפל
        _connect(c, "instagram")
        with session_scope() as s:
            assert s.query(SocialAccount).count() == 2
    finally:
        _unregister()


def test_partial_connect_and_missing_permissions():
    _reset_accounts()
    m = FakeMeta()
    m.pages[0].pop("instagram_business_account")
    _register(m)
    try:
        c = _api()
        loc = _connect(c, "instagram").headers["location"]
        assert "connected=instagram" in loc and "publish_error=meta_no_instagram" in loc, loc
        assert set(_accounts(c)) == {"facebook"}           # העמוד חובר
        m.granted = ["instagram_basic"]                    # המשתמש ביטל הרשאות בחלון
        loc = _connect(c, "facebook").headers["location"]
        assert "publish_error=scope_missing" in loc, loc
    finally:
        _unregister()


def test_discovery_follows_paging_and_respects_granted_scopes():
    m = FakeMeta()
    m.next_page = [{"id": "P3", "name": "Third", "access_token": "PAGE-TOKEN-3", "tasks": ["CREATE_CONTENT"]}]
    cl = _client(m)
    t = TokenSet(access_token="LONG-USER", refresh_token="LONG-USER",
                 scopes=("pages_show_list", "pages_manage_posts"))
    found = cl.discover(t)
    assert [(p, a.external_id) for p, a, _ in found] == [("facebook", "P1"), ("facebook", "P3")]


def _job(c, cid: str, platform: str, **target) -> str:
    acc = _accounts(c)[platform]
    body = {"clip_id": cid, "targets": [{"account_id": acc["id"], "title": "Hello",
                                         "description": "World", "tags": ["polixor", "clips"], **target}]}
    body.update({k: target.pop(k) for k in list(target) if k in ("mode", "schedule_at")})
    r = c.post("/api/publish/jobs", headers=H, json=body)
    assert r.status_code == 201, r.text
    return r.json()["jobs"][0]


def test_instagram_reel_container_upload_poll_and_publish_once():
    _reset_accounts()
    m = FakeMeta()
    _register(m)
    try:
        c = _api()
        _connect(c)
        cid = _clip(name="ig.mp4")
        jid = _job(c, cid, "instagram", options={"cover_frame_seconds": 2.5})
        service.tick(SETTINGS.get())
        with session_scope() as s:
            j = s.get(PublishJob, jid)
            assert j.status == "processing" and j.next_attempt_at > datetime.utcnow()
        assert m.last_container["media_type"] == "REELS" and m.last_container["upload_type"] == "resumable"
        assert m.last_container["thumb_offset"] == "2500" and m.last_container["share_to_feed"] == "true"
        assert m.last_container["caption"] == "Hello\n\nWorld\n\n#polixor #clips"
        assert m.media_publish_calls == 0
        with session_scope() as s:
            s.get(PublishJob, jid).next_attempt_at = datetime.utcnow()
        service.tick(SETTINGS.get())
        item = c.get(f"/api/publish/jobs?clip_id={cid}").json()["items"][0]
        assert item["status"] == "published" and item["remote_url"] == "https://www.instagram.com/reel/abc/"
        assert m.media_publish_calls == 1 and m.container_n == 1
        with session_scope() as s:
            assert s.get(PublishJob, jid).attempts == 1           # בדיקת עיבוד לא נספרת כניסיון
            assert s.query(Notification).filter(Notification.kind == "publish_complete").count() >= 1
    finally:
        _unregister()


def test_lost_publish_response_is_not_published_twice():
    _reset_accounts()
    m = FakeMeta()
    m.container_statuses = ["FINISHED"]
    m.lose_publish_response = True
    _register(m)
    try:
        c = _api()
        _connect(c)
        cid = _clip(name="lost.mp4")
        jid = _job(c, cid, "instagram")
        service.tick(SETTINGS.get())
        with session_scope() as s:
            j = s.get(PublishJob, jid)
            assert j.status == "queued" and j.options["checkpoint"]["publishing"] is True
            j.next_attempt_at = datetime.utcnow()
        service.tick(SETTINGS.get())
        with session_scope() as s:
            j = s.get(PublishJob, jid)
            assert j.status == "published" and j.remote_id == "M1"
        assert m.media_publish_calls == 1 and m.container_n == 1     # פעם אחת בלבד
    finally:
        _unregister()


def test_crash_mid_upload_resumes_from_checkpoint_without_duplicate():
    _reset_accounts()
    m = FakeMeta()
    m.container_statuses = ["FINISHED"]
    _register(m)
    try:
        c = _api()
        _connect(c)
        cid = _clip(name="crash.mp4")
        jid = _job(c, cid, "instagram")
        # השרת נפל אחרי שה-container נוצר
        with session_scope() as s:
            j = s.get(PublishJob, jid)
            j.status, j.locked_until = "uploading", datetime.utcnow() - timedelta(minutes=1)
            j.options = {**j.options, "checkpoint": {"container": "C9", "uri": "", "uploaded": True}}
        service.tick(SETTINGS.get())
        with session_scope() as s:
            assert s.get(PublishJob, jid).status == "published"
        assert m.container_n == 0 and m.media_publish_calls == 1      # לא נוצר container חדש
    finally:
        _unregister()


def test_instagram_limit_error_and_expired_container():
    m = FakeMeta()
    p = InstagramProvider(client=_client(m))
    path = _media(name="lim.mp4")
    tok = TokenSet(access_token="PAGE-TOKEN-1")

    def req(**kw):
        return PublishRequest(media_path=path, format="short", title="T", description="", tags=[],
                              privacy="public", duration=20, width=1080, height=1920,
                              account={"external_id": "IG1"}, checkpoint={}, **kw)
    m.quota = 100
    try:
        p.publish(tok, req())
        raise AssertionError
    except PublishError as e:
        assert e.message_key == "publishing.error.quota" and m.container_n == 0
    m.quota = 0
    m.container_statuses = ["ERROR"]
    r = req()
    try:
        p.publish(tok, r)
        raise AssertionError
    except PublishError as e:
        assert e.kind == "permanent" and r.checkpoint["container"] == ""   # ניסיון הבא – container חדש
    m.container_statuses = ["EXPIRED"]
    r = req()
    try:
        p.publish(tok, r)
        raise AssertionError
    except PublishError as e:
        assert e.kind == "retry"


def test_facebook_reel_now_and_scheduled_on_platform():
    _reset_accounts()
    m = FakeMeta()
    _register(m)
    try:
        c = _api()
        _connect(c)
        cid = _clip(name="reel.mp4", duration=45)
        jid = _job(c, cid, "facebook")
        service.tick(SETTINGS.get())
        with session_scope() as s:
            assert s.get(PublishJob, jid).status == "processing"
            s.get(PublishJob, jid).next_attempt_at = datetime.utcnow()
        service.tick(SETTINGS.get())
        item = [i for i in c.get(f"/api/publish/jobs?clip_id={cid}").json()["items"] if i["id"] == jid][0]
        assert item["status"] == "published" and item["remote_url"] == "https://www.facebook.com/reel/R1"
        assert m.reel_finish["video_state"] == "PUBLISHED" and "#polixor" in m.reel_finish["description"]
        # תזמון – Facebook מפרסם בעצמו
        m2 = FakeMeta()
        _register(m2)
        when = datetime.now(timezone.utc) + timedelta(days=2)
        cid2 = _clip(name="reel2.mp4", duration=30)
        pf = c.post("/api/publish/preflight", headers=H, json={
            "clip_id": cid2, "mode": "schedule", "schedule_at": when.isoformat(),
            "targets": [{"account_id": _accounts(c)["facebook"]["id"], "title": "Later"}]}).json()
        assert pf["ok"] and pf["targets"][0]["schedule_by"] == "platform"
        jid2 = _job(c, cid2, "facebook", mode="schedule", schedule_at=when.isoformat())
        service.tick(SETTINGS.get())
        with session_scope() as s:
            assert s.get(PublishJob, jid2).status == "scheduled_on_platform"
        assert m2.reel_finish["video_state"] == "SCHEDULED"
        assert abs(int(m2.reel_finish["scheduled_publish_time"]) - int(when.timestamp())) <= 1
    finally:
        _unregister()


def test_facebook_reel_and_schedule_validation():
    p = FacebookProvider(client=_client(FakeMeta()))
    path = _media(name="v.mp4")

    def keys(**kw):
        base = dict(media_path=path, format="short", title="T", description="", tags=[], privacy="public",
                    duration=30, width=1080, height=1920)
        base.update(kw)
        return [x["key"] for x in p.validate(PublishRequest(**base))]
    assert keys() == []
    assert "meta_too_short" in keys(duration=2)
    assert "facebook_reel_not_vertical" in keys(width=1920, height=1080)
    assert "facebook_reel_resolution" in keys(width=360, height=640)
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)
    far = datetime.now(timezone.utc) + timedelta(days=40)
    assert keys(publish_at=soon) == ["facebook_schedule_too_soon"]
    assert keys(publish_at=far) == ["schedule_too_far"]
    assert keys(format="long", width=1920, height=1080, duration=600, publish_at=far) == []  # וידאו: עד 6 חודשים
    assert p.capabilities().max_seconds["short"] == 90.0
    assert "meta_caption_too_long" in keys(description="x" * 2300)
    assert "meta_too_many_hashtags" in keys(tags=[f"t{i}" for i in range(31)])


def test_facebook_long_video_resumes_upload_session_and_is_not_created_twice():
    _reset_accounts()
    m = FakeMeta()
    m.fail_upload_once = True
    _register(m)
    try:
        c = _api()
        _connect(c)
        cid = _clip(kind=ClipKind.LONG, duration=600, w=1920, h=1080, name="long.mp4")
        jid = _job(c, cid, "facebook")
        service.tick(SETTINGS.get())                       # ההעלאה נקטעה באמצע
        with session_scope() as s:
            j = s.get(PublishJob, jid)
            assert j.status == "queued" and j.options["checkpoint"]["session"] == "upload:S1"
            j.next_attempt_at = datetime.utcnow()
        partial = m.session_received
        service.tick(SETTINGS.get())
        with session_scope() as s:
            j = s.get(PublishJob, jid)
            assert j.status == "published" and j.remote_url == "https://www.facebook.com/P1/videos/V1"
        uploads = [x for x in m.calls if x.url.path == f"/{V}/upload:S1" and x.method == "POST"]
        assert int(uploads[-1].headers["file_offset"]) == partial > 0     # ממשיך מהמקום שהתקבל
        assert len(m.video_posts) == 1 and m.video_posts[0]["fbuploader_video_file_chunk"] == "HANDLE-1"
        assert m.video_posts[0]["title"] == "Hello" and m.video_posts[0]["published"] == "true"
        assert any(x.url.path == f"/{V}/V1/thumbnails" for x in m.calls)
        # ריצה נוספת (למשל אחרי ניסיון חוזר ידני) לא יוצרת וידאו שני
        with session_scope() as s:
            j = s.get(PublishJob, jid)
            j.status = "queued"
        service.run(jid, SETTINGS.get())
        assert len(m.video_posts) == 1
    finally:
        _unregister()


def test_facebook_long_video_scheduled_and_thumbnail_failure_is_not_fatal():
    m = FakeMeta()
    m.thumb_status = 400
    p = FacebookProvider(client=_client(m))
    path = _media(name="sched_long.mp4")
    thumb = Path(DATA) / "t.jpg"
    thumb.write_bytes(b"\xff\xd8" + b"0" * 100)
    when = datetime.now(timezone.utc) + timedelta(days=60)
    r = p.publish(TokenSet(access_token="PAGE-TOKEN-1", refresh_token="LONG-USER"),
                  PublishRequest(media_path=path, format="long", title="Full", description="Desc", tags=[],
                                 privacy="public", duration=900, width=1920, height=1080, publish_at=when,
                                 account={"external_id": "P1"}, checkpoint={},
                                 options={"thumbnail_path": str(thumb)}))
    assert r.state == "scheduled_on_platform"
    assert m.video_posts[0]["published"] == "false"
    assert int(m.video_posts[0]["scheduled_publish_time"]) == int(when.timestamp())
    # בלי טוקן משתמש (למשל חובר לפני הרבה זמן) – בקשת חיבור מחדש, לא קריסה
    try:
        p.publish(TokenSet(access_token="PAGE-TOKEN-1"),
                  PublishRequest(media_path=path, format="long", title="x", description="", tags=[],
                                 privacy="public", duration=900, width=1920, height=1080,
                                 account={"external_id": "P1"}, checkpoint={}))
        raise AssertionError
    except PublishError as e:
        assert e.kind == "auth"


def test_error_mapping():
    def err(status, body):
        return MetaClient.error(httpx.Response(status, json={"error": body}), "x")
    assert err(400, {"code": 190}).kind == "auth"
    assert err(403, {"code": 200}).message_key == "publishing.error.scope_missing"
    assert err(400, {"code": 9, "error_subcode": 2207042}).message_key == "publishing.error.quota"
    assert err(400, {"code": 4}).kind == "retry"
    assert err(500, {"code": 2, "is_transient": True}).kind == "retry"
    e = err(400, {"code": 100, "error_subcode": 33, "message": "token=SECRET"})
    assert e.kind == "permanent" and e.params["reason"] == "100/33" and "SECRET" not in e.detail


def test_revoked_page_token_asks_to_reconnect_with_notification():
    _reset_accounts()
    m = FakeMeta()
    m.errors["reel_start"] = (400, {"code": 190, "message": "Error validating access token"})
    _register(m)
    try:
        c = _api()
        _connect(c)
        cid = _clip(name="revoked.mp4")
        with session_scope() as s:
            n = s.query(Notification).filter(Notification.kind == "account_reconnect").count()
        _job(c, cid, "facebook")
        service.tick(SETTINGS.get())
        with session_scope() as s:
            assert s.query(SocialAccount).filter_by(platform="facebook").one().status == "reconnect_required"
            assert s.query(Notification).filter(Notification.kind == "account_reconnect").count() == n + 1
    finally:
        _unregister()


def test_disconnect_revokes_only_when_last_shared_account():
    _reset_accounts()
    m = FakeMeta()
    _register(m)
    try:
        c = _api()
        _connect(c)
        accs = _accounts(c)
        c.post(f"/api/publish/accounts/{accs['instagram']['id']}/disconnect", headers=H)
        assert m.revoked == 0                              # העמוד עדיין משתמש באותו חיבור
        c.post(f"/api/publish/accounts/{accs['facebook']['id']}/disconnect", headers=H)
        assert m.revoked == 1
    finally:
        _unregister()


def test_capabilities_drive_the_ui():
    ig = InstagramProvider(client=_client(FakeMeta())).capabilities()
    fb = FacebookProvider(client=_client(FakeMeta())).capabilities()
    assert ig.privacy == ("public",) and not ig.native_scheduling and "cover_frame" in ig.notes
    assert fb.native_scheduling and "cover_frame" not in fb.notes and fb.privacy == ("public",)
    c = _api()
    plats = {p["id"]: p for p in c.get("/api/publish/platforms").json()["platforms"]}
    assert plats["instagram"]["available"] and plats["facebook"]["available"]
    assert plats["instagram"]["configured"] and plats["tiktok"]["planned_stage"] == 9


def test_caption_builder():
    r = PublishRequest(media_path=Path("x"), format="short", title="שלום", description="עולם",
                       tags=["#קליפ", "polixor", " "], privacy="public", duration=1, width=1, height=1)
    assert caption(r) == "שלום\n\nעולם\n\n#קליפ #polixor"


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, globals()[n]) for n in list(globals()) if n.startswith("test_")]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    for f in ("meta_app_id", "meta_app_secret"):
        SECRETS.delete(f)
    print(f"\n{passed}/{len(fns)} בדיקות Meta עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
