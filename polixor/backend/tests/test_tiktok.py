"""
בדיקות לספק TikTok (שלב 9) – מול שרת TikTok מדומה (httpx.MockTransport).
אין בקשת רשת אמיתית ואין פרסום אמיתי.

  * OAuth: כתובת Login Kit (client_key, הרשאות, PKCE עם challenge ב-hex
    כמו ש-TikTok דורש), החלפת קוד עם code_verifier, הרשאת video.publish
    חובה, חידוש (24 שעות / 365 יום), ביטול.
  * פרטי יוצר: כינוי, אפשרויות פרטיות (לא מבוקר → רק SELF_ONLY),
    אינטראקציות שכובו, אורך מרבי – נאכפים בבדיקה המוקדמת ובפרסום.
  * Direct Post: post_info (כיתוב, פרטיות, אינטראקציות כבויות כברירת מחדל,
    גילוי מסחרי, תווית AI, כריכה), חלקים לפי כללי TikTok, PUT לפי הסדר עם
    Content-Range, סטטוס → PUBLISH_COMPLETE וקישור.
  * בלי כפילות: נפילה אחרי חלק → ממשיכים מהחלק הבא; publish_id שהועלה לא
    מאותחל שוב; כתובת העלאה שפגה → init חדש; FAILED → init חדש.
  * שגיאות: פרטיות לא מותרת, לא מבוקר, מכסה יומית, הרשאה חסרה, 401.
  * פרטיות חובה (אין ברירת מחדל); תוכן ממומן לא "רק אני"; גילוי מסחרי דורש בחירה.

הרצה:  python3 tests/test_tiktok.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = tempfile.mkdtemp(prefix="pxtt_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_SCHEDULER"] = "0"

import httpx                                                         # noqa: E402

from polixor.config import PATHS, SECRETS, SETTINGS                  # noqa: E402
from polixor.db import init_db, session_scope                        # noqa: E402
from polixor.models import (Clip, ClipKind, ClipStatus, Job, JobStatus,  # noqa: E402
                            OAuthState, PublishJob, SocialAccount, new_id)
from polixor.services.publishing import oauth, registry, service, vault  # noqa: E402
from polixor.services.publishing.base import PublishError, PublishRequest, TokenSet  # noqa: E402
from polixor.services.publishing.tiktok import (API, TikTokProvider, chunk_plan,  # noqa: E402
                                                hex_challenge)

PATHS.ensure()
init_db()
KEY, SECRET = "awtiktokkey", "tiktok-secret-not-real"
SECRETS.set("tiktok_client_key", KEY)
SECRETS.set("tiktok_client_secret", SECRET)
H = {"X-Polixor-Request": "1"}
MB = 1024 * 1024


class FakeTikTok:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.scope = "user.info.basic,video.publish"
        self.creator = {"creator_nickname": "Lior", "creator_username": "lior.live",
                        "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                        "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False,
                        "max_video_post_duration_sec": 600}
        self.inits: list[dict] = []
        self.received: dict[str, int] = {}
        self.fail_chunk_once: int | None = None
        self.expired_url = False
        self.status_seq = ["PROCESSING_UPLOAD", "PUBLISH_COMPLETE"]
        self.init_error: str | None = None
        self.revoked = 0
        self.refreshes = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        url = str(req.url)
        if url == f"{API}/oauth/token/":
            f = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            assert f["client_key"] == KEY and f["client_secret"] == SECRET
            if f["grant_type"] == "authorization_code":
                assert len(f["code_verifier"]) >= 43
            else:
                self.refreshes += 1
            return httpx.Response(200, json={"access_token": f"act.{len(self.calls)}", "expires_in": 86400,
                                             "refresh_token": "rft.1", "refresh_expires_in": 31536000,
                                             "open_id": "OPEN1", "scope": self.scope, "token_type": "Bearer"})
        if url == f"{API}/oauth/revoke/":
            self.revoked += 1
            return httpx.Response(200, json={})
        if url.startswith(f"{API}/user/info/"):
            return httpx.Response(200, json={"data": {"user": {"open_id": "OPEN1", "display_name": "Lior",
                                                               "avatar_url": "https://p/a.jpg"}},
                                             "error": {"code": "ok"}})
        if url == f"{API}/post/publish/creator_info/query/":
            return httpx.Response(200, json={"data": self.creator, "error": {"code": "ok"}})
        if url == f"{API}/post/publish/video/init/":
            if self.init_error:
                code, self.init_error = self.init_error, None
                return httpx.Response(403, json={"error": {"code": code, "message": "x"}})
            body = json.loads(req.content)
            self.inits.append(body)
            pid = f"v_pub_{len(self.inits)}"
            self.received[pid] = 0
            return httpx.Response(200, json={"data": {"publish_id": pid,
                                                      "upload_url": f"https://open-upload.tiktokapis.com/upload/?id={pid}"},
                                             "error": {"code": "ok"}})
        if url.startswith("https://open-upload.tiktokapis.com/upload/"):
            pid = url.split("id=")[1]
            if self.expired_url:
                self.expired_url = False
                return httpx.Response(404, json={})
            cr = req.headers["content-range"]
            start = int(cr.split(" ")[1].split("-")[0])
            assert start == self.received[pid], (start, self.received[pid])     # לפי הסדר, בלי חורים
            assert req.headers["content-type"] == "video/mp4"
            if self.fail_chunk_once is not None and start >= self.fail_chunk_once:
                self.fail_chunk_once = None
                raise httpx.ConnectError("dropped")
            self.received[pid] = start + len(req.content)
            return httpx.Response(206 if not cr.endswith(f"/{self.received[pid]}") else 201)
        if url == f"{API}/post/publish/status/fetch/":
            st = self.status_seq.pop(0) if len(self.status_seq) > 1 else self.status_seq[0]
            body = {"status": st}
            if st == "PUBLISH_COMPLETE":
                body["publicaly_available_post_id"] = [7123456789]
            if st == "FAILED":
                body["fail_reason"] = "file_format_check_failed"
            return httpx.Response(200, json={"data": body, "error": {"code": "ok"}})
        raise AssertionError(f"unexpected request {req.method} {url}")


def _prov(t: FakeTikTok, **kw) -> TikTokProvider:
    return TikTokProvider(http=httpx.Client(transport=httpx.MockTransport(t.handler)), chunk=5 * MB, **kw)


def _media(size: int, name: str) -> Path:
    p = Path(DATA) / name
    p.write_bytes(os.urandom(size))
    return p


def _req(path: Path, **kw) -> PublishRequest:
    base = dict(media_path=path, format="short", title="Hi", description="there", tags=["fyp"],
                privacy="SELF_ONLY", duration=30, width=1080, height=1920, checkpoint={})
    base.update(kw)
    return PublishRequest(**base)


TOK = TokenSet(access_token="act.1", refresh_token="rft.1")


# --------------------------------------------------------------------------
def test_login_kit_url_uses_hex_pkce_challenge():
    p = _prov(FakeTikTok())
    assert p.configured()
    st, ch = oauth.create_state("tiktok", redirect_uri="https://me/cb", challenge_fn=p.pkce_challenge)
    with session_scope() as s:
        verifier = vault.decrypt(s.get(OAuthState, st).verifier_enc)
    assert ch == hashlib.sha256(verifier.encode()).hexdigest() and len(ch) == 64     # hex, לא base64url
    url = p.authorize_url(state=st, redirect_uri="https://me/cb", code_challenge=ch)
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?")
    assert q["client_key"] == [KEY] and q["scope"] == ["user.info.basic,video.publish"]
    assert q["code_challenge_method"] == ["S256"] and SECRET not in url
    assert hex_challenge("abc") == hashlib.sha256(b"abc").hexdigest()


def test_exchange_refresh_and_scope():
    t = FakeTikTok()
    p = _prov(t)
    tok = p.exchange_code(code="c", redirect_uri="https://me/cb", code_verifier="v" * 50)
    assert tok.refresh_token == "rft.1"
    assert tok.expires_at - datetime.now(tok.expires_at.tzinfo) <= timedelta(hours=24)
    assert tok.refresh_expires_at - datetime.now(tok.expires_at.tzinfo) > timedelta(days=360)
    assert p.refresh("rft.1").access_token and t.refreshes == 1
    t.scope = "user.info.basic"
    try:
        p.exchange_code(code="c", redirect_uri="x", code_verifier="v" * 50)
        raise AssertionError
    except PublishError as e:
        assert e.message_key == "publishing.error.scope_missing"


def test_chunk_plan_follows_tiktok_rules():
    assert chunk_plan(3 * MB) == (3 * MB, 1)                   # מתחת ל-5MB – חלק אחד
    assert chunk_plan(12 * MB, 5 * MB) == (5 * MB, 2)          # 5 + 7 (האחרון גדול יותר)
    size, n = chunk_plan(200 * MB)
    assert 5 * MB <= size <= 64 * MB and 200 * MB - size * (n - 1) <= 128 * MB


def test_creator_details_unaudited_only_self_only():
    t = FakeTikTok()
    d = _prov(t).account_details(TOK)
    assert d["nickname"] == "Lior" and d["privacy_options"] == ["SELF_ONLY"] and d["duet_disabled"]
    d = _prov(t, audited=True).account_details(TOK)
    assert d["privacy_options"] == ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
    assert d["max_video_seconds"] == 600


def test_direct_post_post_info_chunks_status_and_link():
    t = FakeTikTok()
    path = _media(12 * MB, "a.mp4")
    r = _req(path, options={"allow_comment": True, "synthetic_media": True, "cover_frame_seconds": 1.5})
    p = _prov(t)
    res = p.publish(TOK, r)
    assert res.state == "processing"                           # עוד בעיבוד
    info = t.inits[0]["post_info"]
    assert info["title"] == "Hi\n\nthere\n\n#fyp" and info["privacy_level"] == "SELF_ONLY"
    assert info["disable_comment"] is False and info["disable_duet"] is True and info["disable_stitch"] is True
    assert info["is_aigc"] is True and info["video_cover_timestamp_ms"] == 1500
    assert info["brand_content_toggle"] is False and info["brand_organic_toggle"] is False
    src = t.inits[0]["source_info"]
    assert src == {"source": "FILE_UPLOAD", "video_size": 12 * MB, "chunk_size": 5 * MB, "total_chunk_count": 2}
    assert t.received["v_pub_1"] == 12 * MB
    res = p.publish(TOK, r)                                    # בדיקת מצב בלבד – בלי init/העלאה חדשים
    assert res.state == "published" and res.url == "https://www.tiktok.com/@lior.live/video/7123456789"
    assert len(t.inits) == 1


def test_interrupted_chunk_resumes_and_expired_url_reinitialises():
    t = FakeTikTok()
    t.status_seq = ["PUBLISH_COMPLETE"]
    path = _media(16 * MB, "b.mp4")
    p = _prov(t)
    r = _req(path)
    t.fail_chunk_once = 5 * MB                                 # החלק השני נופל
    try:
        p.publish(TOK, r)
        raise AssertionError
    except PublishError as e:
        assert e.kind == "retry" and r.checkpoint["sent"] == 1
    res = p.publish(TOK, r)                                    # ממשיך מהחלק השני
    assert res.state == "published" and len(t.inits) == 1 and t.received["v_pub_1"] == 16 * MB
    # כתובת העלאה שפגה (שעה) – ה-publish_id הישן לא יפורסם; init חדש
    t2 = FakeTikTok()
    t2.status_seq = ["PUBLISH_COMPLETE"]
    p2 = _prov(t2)
    r2 = _req(path)
    t2.expired_url = True
    try:
        p2.publish(TOK, r2)
        raise AssertionError
    except PublishError as e:
        assert e.kind == "retry" and r2.checkpoint["publish_id"] == ""
    assert p2.publish(TOK, r2).state == "published" and len(t2.inits) == 2


def test_failed_status_and_error_mapping():
    t = FakeTikTok()
    t.status_seq = ["FAILED"]
    p = _prov(t)
    r = _req(_media(2 * MB, "c.mp4"))
    try:
        p.publish(TOK, r)
        raise AssertionError
    except PublishError as e:
        assert e.kind == "permanent" and e.params["reason"] == "file_format_check_failed"
        assert r.checkpoint["publish_id"] == ""                # ניסיון הבא – init חדש
    for code, key, kind in [("spam_risk_too_many_posts", "publishing.error.quota", "permanent"),
                            ("unaudited_client_can_only_post_to_private_accounts",
                             "publishing.error.tiktok_unaudited", "permanent"),
                            ("privacy_level_option_mismatch", "publishing.error.tiktok_privacy", "permanent"),
                            ("scope_not_authorized", "publishing.error.scope_missing", "auth"),
                            ("access_token_invalid", "publishing.error.reconnect", "auth"),
                            ("rate_limit_exceeded", "publishing.error.temporary", "retry")]:
        t = FakeTikTok()
        t.init_error = code
        try:
            _prov(t).publish(TOK, _req(_media(1 * MB, "d.mp4")))
            raise AssertionError(code)
        except PublishError as e:
            assert (e.message_key, e.kind) == (key, kind), (code, e.message_key, e.kind)


def test_creator_rules_enforced_before_posting():
    t = FakeTikTok()
    t.creator["max_video_post_duration_sec"] = 60
    p = _prov(t)
    r = _req(_media(1 * MB, "e.mp4"), duration=90, options={"allow_duet": True})
    keys = [x["key"] for x in p.live_checks(TOK, r)]
    assert keys == ["tiktok_too_long_for_creator", "tiktok_interaction_disabled"]
    try:
        p.publish(TOK, r)
        raise AssertionError
    except PublishError as e:
        assert e.kind == "permanent" and not t.inits        # לא אותחל שום פוסט


def test_commercial_disclosure_rules():
    p = _prov(FakeTikTok(), audited=True)
    path = _media(1 * MB, "f.mp4")
    assert [x["key"] for x in p.validate(_req(path, options={"commercial": True}))] == ["tiktok_commercial_choice"]
    assert [x["key"] for x in p.validate(_req(path, options={"commercial": True, "brand_content": True}))] \
        == ["tiktok_branded_not_private"]
    assert p.validate(_req(path, privacy="PUBLIC_TO_EVERYONE",
                           options={"commercial": True, "brand_content": True})) == []
    t = FakeTikTok()
    t.status_seq = ["PUBLISH_COMPLETE"]
    _prov(t, audited=True).publish(TOK, _req(path, privacy="PUBLIC_TO_EVERYONE",
                                             options={"commercial": True, "brand_organic": True}))
    info = t.inits[0]["post_info"]
    assert info["brand_organic_toggle"] is True and info["brand_content_toggle"] is False


def test_full_flow_privacy_required_polixor_schedule_and_no_duplicate():
    from fastapi.testclient import TestClient

    from polixor.main import app

    t = FakeTikTok()
    registry.register(_prov(t))
    try:
        c = TestClient(app, base_url="http://testserver")
        r = c.post("/api/publish/accounts/tiktok/connect", json={}, headers=H)
        q = parse_qs(urlsplit(r.json()["auth_url"]).query)
        assert len(q["code_challenge"][0]) == 64
        loc = c.get(f"/api/publish/oauth/tiktok/callback?state={q['state'][0]}&code=CODE",
                    follow_redirects=False).headers["location"]
        assert "connected=tiktok" in loc
        acc = [a for a in c.get("/api/publish/accounts").json()["accounts"] if a["platform"] == "tiktok"][0]
        assert "act." not in json.dumps(acc) and "rft" not in json.dumps(acc)
        det = c.get(f"/api/publish/accounts/{acc['id']}/details").json()["details"]
        assert det["nickname"] == "Lior" and det["privacy_options"] == ["SELF_ONLY"]
        path = _media(2 * MB, "flow.mp4")
        jid, cid = new_id(), new_id()
        with session_scope() as s:
            s.add(Job(id=jid, title="S", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[]))
            s.flush()
            s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="Clip",
                       file_path=str(path), file_size=path.stat().st_size, duration=20, width=1080, height=1920))
        # בלי בחירת פרטיות – חסום (אין ברירת מחדל)
        pf = c.post("/api/publish/preflight", headers={**H, "X-Polixor-Lang": "en"}, json={
            "clip_id": cid, "targets": [{"account_id": acc["id"], "title": "T"}]}).json()
        assert not pf["ok"] and pf["targets"][0]["issues"][0]["key"] == "privacy_required"
        assert pf["targets"][0]["warnings"][0]["key"] == "tiktok_private_until_audit"
        # פרטיות שלא הוצעה (לא מבוקר) – חסום
        pf = c.post("/api/publish/preflight", headers=H, json={
            "clip_id": cid, "targets": [{"account_id": acc["id"], "title": "T",
                                         "privacy": "PUBLIC_TO_EVERYONE"}]}).json()
        assert not pf["ok"]
        assert pf["targets"][0]["issues"][0]["key"] == "privacy_unsupported"
        # תזמון – Polixor מבצע (אין תזמון ב-API)
        when = (datetime.utcnow() + timedelta(hours=2)).isoformat() + "+00:00"
        target = {"account_id": acc["id"], "title": "T", "privacy": "SELF_ONLY"}
        pf = c.post("/api/publish/preflight", headers=H, json={
            "clip_id": cid, "mode": "schedule", "schedule_at": when, "targets": [target]}).json()
        assert pf["ok"] and pf["targets"][0]["schedule_by"] == "polixor", pf
        r = c.post("/api/publish/jobs", headers=H, json={"clip_id": cid, "targets": [target]})
        job = r.json()["jobs"][0]
        service.tick(SETTINGS.get())
        with session_scope() as s:
            j = s.get(PublishJob, job)
            assert j.status == "processing"
            j.next_attempt_at = datetime.utcnow()
        service.tick(SETTINGS.get())
        with session_scope() as s:
            j = s.get(PublishJob, job)
            assert j.status == "published" and "tiktok.com/@lior.live/video/" in j.remote_url
        assert len(t.inits) == 1
        # ניתוק מבטל את ההרשאה ב-TikTok
        c.post(f"/api/publish/accounts/{acc['id']}/disconnect", headers=H)
        assert t.revoked == 1
    finally:
        registry.unregister("tiktok")


def test_expired_access_token_refreshed_before_posting():
    t = FakeTikTok()
    t.status_seq = ["PUBLISH_COMPLETE"]
    registry.register(_prov(t))
    try:
        aid, jid, cid = new_id(), new_id(), new_id()
        path = _media(1 * MB, "exp.mp4")
        with session_scope() as s:
            s.add(SocialAccount(id=aid, platform="tiktok", external_id="OPEN1", display_name="Lior",
                                access_token_enc=vault.encrypt("act.old"), refresh_token_enc=vault.encrypt("rft.1"),
                                token_expires_at=datetime.utcnow() - timedelta(minutes=1)))
            s.add(Job(id=jid, title="S", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[]))
            s.flush()
            s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="C",
                       file_path=str(path), file_size=path.stat().st_size, duration=20, width=1080, height=1920))
        out = service.create(cid, [{"account_id": aid, "title": "T", "privacy": "SELF_ONLY"}], SETTINGS.get())
        service.tick(SETTINGS.get())
        with session_scope() as s:
            assert s.get(PublishJob, out["jobs"][0]).status == "published"
        assert t.refreshes >= 1
        assert t.calls[-1].headers["authorization"] != "Bearer act.old"
    finally:
        registry.unregister("tiktok")


def test_platform_listed_as_available():
    from fastapi.testclient import TestClient

    from polixor.main import app

    tt = [x for x in TestClient(app).get("/api/publish/platforms").json()["platforms"] if x["id"] == "tiktok"][0]
    assert tt["available"] and tt["configured"] and tt["planned_stage"] is None
    caps = tt["capabilities"]
    assert caps["privacy_required"] and not caps["native_scheduling"] and "music_confirmation" in caps["notes"]


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
    for f in ("tiktok_client_key", "tiktok_client_secret"):
        SECRETS.delete(f)
    print(f"\n{passed}/{len(fns)} בדיקות TikTok עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
