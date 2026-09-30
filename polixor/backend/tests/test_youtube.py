"""
בדיקות לספק YouTube (שלב 7) – מול שרת Google מדומה (httpx.MockTransport).
אין שום בקשת רשת אמיתית ושום העלאה אמיתית: כל כתובת שלא מוכרת לבדיקה נכשלת.

  * OAuth: כתובת ההרשאה (PKCE S256, offline, הרשאות מינימליות, בלי סוד),
    החלפת קוד, refresh token חובה, הרשאת העלאה חובה, חידוש, invalid_grant.
  * ערוץ: שם, כינוי, תמונה; חשבון בלי ערוץ.
  * העלאה ניתנת לחידוש: חלקים עם Content-Range, 308 + Range, תקלה זמנית
    באמצע → שאילתת מצב והמשך מאותו בית (לא מהתחלה).
  * מטא-דאטה: פרטיות, publishAt מכריח private, made for kids, תוכן AI,
    שפה, תגיות; קישור Shorts/watch; תמונה ממוזערת רק לארוך ובלי להכשיל.
  * שגיאות: 401 → חיבור מחדש; מכסה → קבוע; 400 → נדחה עם סיבה; 5xx → זמני.
  * אזהרות: פרויקט שלא עבר ביקורת → פרטי; ארוך מ-15 דקות.
  * זרימה מלאה דרך ה-API והמתזמן, עם התראה.

הרצה:  python3 tests/test_youtube.py
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
DATA = tempfile.mkdtemp(prefix="pxyt_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_SCHEDULER"] = "0"

import httpx                                                         # noqa: E402

from polixor.config import PATHS, SECRETS, SETTINGS, AppSettings     # noqa: E402
from polixor.db import init_db, session_scope                        # noqa: E402
from polixor.models import (Clip, ClipKind, ClipStatus, Job, JobStatus,  # noqa: E402
                            Notification, PublishJob, SocialAccount, new_id)
from polixor.services.publishing import registry, service           # noqa: E402
from polixor.services.publishing.base import PublishError, PublishRequest, TokenSet  # noqa: E402
from polixor.services.publishing.youtube import YouTubeProvider     # noqa: E402

PATHS.ensure()
init_db()
CID, SECRET = "123.apps.googleusercontent.com", "gsecret-not-real"
SECRETS.set("youtube_client_id", CID)
SECRETS.set("youtube_client_secret", SECRET)
H = {"X-Polixor-Request": "1"}


class FakeGoogle:
    """שרת Google מדומה. כל בקשה נרשמת; כתובת לא מוכרת – כשל."""

    def __init__(self, size: int = 0) -> None:
        self.calls: list[httpx.Request] = []
        self.received = 0
        self.size = size
        self.fail_put_once_at: int | None = None
        self.upload_status = 200
        self.upload_error: tuple[int, str] | None = None
        self.token_status = 200
        self.refresh_token = "1//refresh-not-real"
        self.scope = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
        self.channels: list = [{"id": "UC123", "snippet": {"title": "My Channel", "customUrl": "@mychannel",
                                                           "thumbnails": {"default": {"url": "https://yt3/x.jpg"}}}}]
        self.thumb_status = 200
        self.init_body: dict = {}
        self.put_ranges: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        url = str(req.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            body = {"access_token": "ya29.access-not-real", "expires_in": 3599, "scope": self.scope,
                    "token_type": "Bearer"}
            if self.refresh_token:
                body["refresh_token"] = self.refresh_token
            return httpx.Response(200, json=body)
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200)
        if url.startswith("https://www.googleapis.com/youtube/v3/channels"):
            return httpx.Response(200, json={"items": self.channels})
        if url.startswith("https://www.googleapis.com/upload/youtube/v3/videos"):
            if self.upload_error:
                code, reason = self.upload_error
                return httpx.Response(code, json={"error": {"errors": [{"reason": reason}]}})
            self.init_body = json.loads(req.content)
            return httpx.Response(200, headers={"Location": "https://upload.example/session/abc"})
        if url.startswith("https://upload.example/session/abc"):
            cr = req.headers.get("content-range", "")
            self.put_ranges.append(cr)
            if cr.startswith("bytes */"):
                return httpx.Response(308, headers={"Range": f"bytes=0-{self.received - 1}"}
                                      if self.received else {})
            start = int(cr.split(" ")[1].split("-")[0])
            assert start == self.received, (start, self.received)      # ממשיכים בדיוק מהמקום
            if self.fail_put_once_at is not None and start >= self.fail_put_once_at:
                self.fail_put_once_at = None
                # חצי מהחלק התקבל ואז נפל
                self.received = start + len(req.content) // 2
                return httpx.Response(503)
            self.received = start + len(req.content)
            if self.received >= self.size:
                return httpx.Response(self.upload_status, json={"id": "vid123", "kind": "youtube#video"})
            return httpx.Response(308, headers={"Range": f"bytes=0-{self.received - 1}"})
        if url.startswith("https://www.googleapis.com/upload/youtube/v3/thumbnails/set"):
            return httpx.Response(self.thumb_status, json={})
        raise AssertionError(f"unexpected request to {url}")        # אין רשת אמיתית


def _provider(g: FakeGoogle, **kw) -> YouTubeProvider:
    return YouTubeProvider(http=httpx.Client(transport=httpx.MockTransport(g.handler)),
                           chunk=256 * 1024, **kw)


def _media(size: int = 700 * 1024, name: str = "v.mp4") -> Path:
    p = Path(DATA) / name
    p.write_bytes(os.urandom(size))
    return p


def _req(path: Path, **kw) -> PublishRequest:
    base = dict(media_path=path, format="short", title="My short", description="desc", tags=["a", "b"],
                privacy="public", duration=30.0, width=1080, height=1920)
    base.update(kw)
    return PublishRequest(**base)


TOK = TokenSet(access_token="ya29.access-not-real", refresh_token="1//refresh-not-real")


# --------------------------------------------------------------------------
def test_authorize_url_uses_pkce_offline_and_minimal_scopes():
    p = _provider(FakeGoogle())
    assert p.configured()
    url = p.authorize_url(state="st4te", redirect_uri="https://me/cb", code_challenge="ch4ll")
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"] == ["ch4ll"]
    assert q["access_type"] == ["offline"] and q["state"] == ["st4te"] and q["client_id"] == [CID]
    assert set(q["scope"][0].split()) == {"https://www.googleapis.com/auth/youtube.upload",
                                          "https://www.googleapis.com/auth/youtube.readonly"}
    assert SECRET not in url


def test_code_exchange_sends_verifier_and_requires_refresh_and_upload_scope():
    g = FakeGoogle()
    p = _provider(g)
    t = p.exchange_code(code="c0de", redirect_uri="https://me/cb", code_verifier="v" * 50)
    form = parse_qs(g.calls[-1].content.decode())
    assert form["code_verifier"] == ["v" * 50] and form["grant_type"] == ["authorization_code"]
    assert form["client_secret"] == [SECRET]
    assert t.refresh_token and t.expires_at > datetime.now(timezone.utc)
    g.refresh_token = ""
    try:
        p.exchange_code(code="c", redirect_uri="x", code_verifier="v")
        raise AssertionError("accepted without refresh token")
    except PublishError as e:
        assert e.message_key == "publishing.error.oauth_failed"
    g.refresh_token, g.scope = "1//r", "https://www.googleapis.com/auth/youtube.readonly"
    try:
        p.exchange_code(code="c", redirect_uri="x", code_verifier="v")
        raise AssertionError("accepted without upload scope")
    except PublishError as e:
        assert e.message_key == "publishing.error.scope_missing"


def test_refresh_and_revoked_grant():
    g = FakeGoogle()
    p = _provider(g)
    t = p.refresh("1//refresh-not-real")
    assert t.access_token and t.refresh_token == "1//refresh-not-real"   # Google לא תמיד מחזיר חדש
    g.token_status = 400
    try:
        p.refresh("1//gone")
        raise AssertionError
    except PublishError as e:
        assert e.kind == "auth"


def test_channel_info_and_no_channel():
    g = FakeGoogle()
    p = _provider(g)
    info = p.account_info(TOK)
    assert (info.external_id, info.display_name, info.handle) == ("UC123", "My Channel", "@mychannel")
    assert g.calls[-1].headers["authorization"] == "Bearer ya29.access-not-real"
    g.channels = []
    try:
        p.account_info(TOK)
        raise AssertionError
    except PublishError as e:
        assert e.message_key == "publishing.error.youtube_no_channel"


def test_resumable_upload_in_chunks_with_metadata():
    path = _media(700 * 1024)
    g = FakeGoogle(size=path.stat().st_size)
    p = _provider(g)
    prog: list[float] = []
    r = p.publish(TOK, _req(path, options={"made_for_kids": False, "synthetic_media": True,
                                            "language": "he"}), on_progress=prog.append)
    assert r.remote_id == "vid123" and r.url == "https://www.youtube.com/shorts/vid123"
    assert r.state == "published"
    init = [c for c in g.calls if "upload/youtube/v3/videos" in str(c.url)][0]
    assert init.url.params["uploadType"] == "resumable" and init.url.params["part"] == "snippet,status"
    assert init.headers["x-upload-content-length"] == str(path.stat().st_size)
    st, sn = g.init_body["status"], g.init_body["snippet"]
    assert st == {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                  "containsSyntheticMedia": True}
    assert sn["title"] == "My short" and sn["tags"] == ["a", "b"] and sn["defaultLanguage"] == "he"
    assert len([x for x in g.put_ranges if not x.startswith("bytes */")]) == 3     # 256K+256K+188K
    assert prog[-1] == 1.0 and prog == sorted(prog)


def test_interrupted_chunk_resumes_from_received_byte():
    path = _media(900 * 1024, "resume.mp4")
    g = FakeGoogle(size=path.stat().st_size)
    g.fail_put_once_at = 256 * 1024
    p = _provider(g)
    r = p.publish(TOK, _req(path))
    assert r.remote_id == "vid123"
    assert any(x.startswith("bytes */") for x in g.put_ranges)          # שאילתת מצב
    starts = [int(x.split(" ")[1].split("-")[0]) for x in g.put_ranges if not x.startswith("bytes */")]
    assert starts[0] == 0 and 256 * 1024 in starts
    assert any(s % (256 * 1024) for s in starts)                        # המשיך מאמצע החלק


def test_schedule_forces_private_and_publish_at():
    path = _media(300 * 1024, "sched.mp4")
    g = FakeGoogle(size=path.stat().st_size)
    when = datetime(2030, 5, 1, 18, 30, tzinfo=timezone.utc)
    r = _provider(g).publish(TOK, _req(path, privacy="public", publish_at=when, format="long",
                                       width=1920, height=1080))
    assert g.init_body["status"]["privacyStatus"] == "private"
    assert g.init_body["status"]["publishAt"] == "2030-05-01T18:30:00Z"
    assert r.state == "scheduled_on_platform" and r.url == "https://www.youtube.com/watch?v=vid123"


def test_thumbnail_only_for_long_and_failure_is_not_fatal():
    thumb = Path(DATA) / "t.jpg"
    thumb.write_bytes(b"\xff\xd8" + b"0" * 1000)
    path = _media(300 * 1024, "th.mp4")
    g = FakeGoogle(size=path.stat().st_size)
    _provider(g).publish(TOK, _req(path, options={"thumbnail_path": str(thumb)}))
    assert not any("thumbnails/set" in str(c.url) for c in g.calls)       # קצר – לא
    g = FakeGoogle(size=path.stat().st_size)
    g.thumb_status = 403
    r = _provider(g).publish(TOK, _req(path, format="long", width=1920, height=1080,
                                       options={"thumbnail_path": str(thumb)}))
    assert any("thumbnails/set" in str(c.url) for c in g.calls) and r.remote_id == "vid123"


def test_error_mapping():
    path = _media(300 * 1024, "err.mp4")
    for (code, reason), kind, key in [((401, "authError"), "auth", "publishing.error.reconnect"),
                                      ((403, "quotaExceeded"), "permanent", "publishing.error.quota"),
                                      ((403, "uploadLimitExceeded"), "permanent", "publishing.error.quota"),
                                      ((400, "invalidTitle"), "permanent", "publishing.error.rejected"),
                                      ((503, "backendError"), "retry", "publishing.error.temporary")]:
        g = FakeGoogle(size=path.stat().st_size)
        g.upload_error = (code, reason)
        try:
            _provider(g).publish(TOK, _req(path))
            raise AssertionError(reason)
        except PublishError as e:
            assert (e.kind, e.message_key) == (kind, key), (reason, e.kind, e.message_key)
            assert "ya29" not in e.detail
            if key == "publishing.error.rejected":
                assert e.params["reason"] == "invalidTitle"


def test_validation_and_warnings():
    p = _provider(FakeGoogle())
    path = _media(1024, "val.mp4")
    assert [x["key"] for x in p.validate(_req(path, width=1920, height=1080))] == ["youtube_short_not_vertical"]
    assert [x["key"] for x in p.validate(_req(path, title="a <b>"))] == ["youtube_title_brackets"]
    w = [x["key"] for x in p.warnings(_req(path, privacy="public"))]
    assert w == ["youtube_private_until_audit"]
    assert p.warnings(_req(path, privacy="private")) == []
    long_w = [x["key"] for x in p.warnings(_req(path, privacy="private", format="long", duration=1200))]
    assert long_w == ["youtube_long_needs_verified"]
    audited = _provider(FakeGoogle(), audited=True)
    assert audited.warnings(_req(path, privacy="public")) == []
    assert audited.capabilities().privacy[0] == "public" and p.capabilities().privacy[0] == "private"
    assert p.capabilities().native_scheduling


def test_registry_lists_youtube_as_available():
    from fastapi.testclient import TestClient

    from polixor.main import app

    c = TestClient(app)
    yt = [x for x in c.get("/api/publish/platforms").json()["platforms"] if x["id"] == "youtube"][0]
    assert yt["available"] and yt["configured"] and yt["planned_stage"] is None
    assert yt["capabilities"]["native_scheduling"] is True


def test_full_flow_through_api_and_scheduler():
    from fastapi.testclient import TestClient

    from polixor.main import app

    path = _media(600 * 1024, "flow.mp4")
    g = FakeGoogle(size=path.stat().st_size)
    registry.register(_provider(g))
    try:
        c = TestClient(app, base_url="http://testserver")
        r = c.post("/api/publish/accounts/youtube/connect", json={}, headers=H)
        url = r.json()["auth_url"]
        q = parse_qs(urlsplit(url).query)
        assert q["redirect_uri"] == ["http://testserver/api/publish/oauth/youtube/callback"]
        # Google מחזיר ל-callback עם code ו-state
        r = c.get(f"/api/publish/oauth/youtube/callback?state={q['state'][0]}&code=c0de",
                  follow_redirects=False)
        assert "connected=youtube" in r.headers["location"], r.headers["location"]
        acc = [a for a in c.get("/api/publish/accounts").json()["accounts"] if a["platform"] == "youtube"][0]
        assert acc["display_name"] == "My Channel" and "ya29" not in json.dumps(acc)
        # פרסום
        jid, cid = new_id(), new_id()
        with session_scope() as s:
            s.add(Job(id=jid, title="S", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[],
                      content_language="he"))
            s.flush()
            s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="Clip",
                       file_path=str(path), file_size=path.stat().st_size, duration=40, width=1080, height=1920))
        pf = c.post("/api/publish/preflight", headers={**H, "X-Polixor-Lang": "en"}, json={
            "clip_id": cid, "targets": [{"account_id": acc["id"], "title": "Hello", "privacy": "public",
                                         "options": {"synthetic_media": True}}]}).json()
        assert pf["ok"] and pf["targets"][0]["warnings"][0]["key"] == "youtube_private_until_audit"
        assert "Google's audit" in pf["targets"][0]["warnings"][0]["text"]
        r = c.post("/api/publish/jobs", headers=H, json={
            "clip_id": cid, "targets": [{"account_id": acc["id"], "title": "Hello", "privacy": "public",
                                         "options": {"synthetic_media": True}}]})
        assert r.status_code == 201
        service.tick(SETTINGS.get())
        item = c.get(f"/api/publish/jobs?clip_id={cid}").json()["items"][0]
        assert item["status"] == "published" and item["remote_url"].endswith("/shorts/vid123")
        assert g.init_body["status"]["containsSyntheticMedia"] is True
        assert g.init_body["snippet"]["defaultLanguage"] == "he"
        with session_scope() as s:
            assert s.query(Notification).filter(Notification.kind == "publish_complete").count() >= 1
        # ניתוק – מבטל את ההרשאה אצל Google
        c.post(f"/api/publish/accounts/{acc['id']}/disconnect", headers=H)
        assert any("oauth2.googleapis.com/revoke" in str(x.url) for x in g.calls)
    finally:
        registry.unregister("youtube")


def test_expired_access_token_is_refreshed_before_upload():
    path = _media(300 * 1024, "exp.mp4")
    g = FakeGoogle(size=path.stat().st_size)
    registry.register(_provider(g))
    try:
        from polixor.services.publishing import vault
        aid, jid, cid = new_id(), new_id(), new_id()
        with session_scope() as s:
            s.add(SocialAccount(id=aid, platform="youtube", external_id="UCx", display_name="Ch",
                                access_token_enc=vault.encrypt("ya29.old"),
                                refresh_token_enc=vault.encrypt("1//refresh-not-real"),
                                token_expires_at=datetime.utcnow() - timedelta(minutes=1)))
            s.add(Job(id=jid, title="S", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[]))
            s.flush()
            s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="C",
                       file_path=str(path), file_size=path.stat().st_size, duration=20, width=1080, height=1920))
        out = service.create(cid, [{"account_id": aid, "title": "T", "privacy": "private"}], SETTINGS.get())
        service.tick(SETTINGS.get())
        with session_scope() as s:
            assert s.get(PublishJob, out["jobs"][0]).status == "published"
            assert s.get(SocialAccount, aid).token_expires_at > datetime.utcnow()
        assert any("oauth2.googleapis.com/token" in str(x.url) for x in g.calls)
        uploads = [x for x in g.calls if "upload/youtube/v3/videos" in str(x.url)]
        assert uploads[0].headers["authorization"] == "Bearer ya29.access-not-real"   # החדש
    finally:
        registry.unregister("youtube")


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
    for f in ("youtube_client_id", "youtube_client_secret"):
        SECRETS.delete(f)
    print(f"\n{passed}/{len(fns)} בדיקות YouTube עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
