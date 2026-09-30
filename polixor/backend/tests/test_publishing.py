"""
בדיקות לתשתית הפרסום (שלב 6). בלי אף פרסום אמיתי: ספק ארגז החול בלבד.

  * OAuth: state חד-פעמי, פג תוקף, שייך לפלטפורמה; PKCE נבדק; הפניה פתוחה
    נחסמת; ביטול בדף האישור.
  * טוקנים: מוצפנים ב-DB, לא יוצאים ב-API, מתחדשים כשפגו; חידוש שנכשל →
    "צריך להתחבר מחדש" + התראה.
  * בדיקה מוקדמת: כותרת, אורך, פורמט, פרטיות, חשבון, מועד בעבר.
  * פרסום עכשיו / תזמון בפלטפורמה / תזמון ב-Polixor; ניסיונות חוזרים עם
    המתנה; כשל קבוע; העלאה שנקטעה לא נשלחת שוב; תזמון שהוחמץ; ביטול;
    ניסיון חוזר; חכירה מונעת ביצוע כפול; ניתוק מבטל מתוזמנים.
  * API: CSRF, יצירה, היסטוריה מקובצת, פרטי מפתחים ממוסכים.
  * התראות לכל מצב, בעברית ובאנגלית.

הרצה:  python3 tests/test_publishing.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = tempfile.mkdtemp(prefix="pxpub_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_SCHEDULER"] = "0"                 # הבדיקות מריצות tick בעצמן

from polixor import i18n                                          # noqa: E402
from polixor.config import PATHS, SECRETS, SETTINGS, AppSettings  # noqa: E402
from polixor.db import init_db, session_scope                     # noqa: E402
from polixor.models import (Clip, ClipKind, ClipStatus, Job, JobStatus,  # noqa: E402
                            Notification, OAuthState, PublishJob, SocialAccount, new_id)
from polixor.services.publishing import oauth, registry, sandbox, service  # noqa: E402

PATHS.ensure()
init_db()
SETTINGS.update({"publish_sandbox": True, "publish_sandbox_native_scheduling": False})
H = {"X-Polixor-Request": "1"}


def S(**kw) -> AppSettings:
    return AppSettings.from_dict({**SETTINGS.get().to_dict(), **kw})


def _client():
    from fastapi.testclient import TestClient

    from polixor.main import app
    return TestClient(app, base_url="http://testserver")


def _clip(kind=ClipKind.SHORT, duration=30.0, w=1080, h=1920, title="Great moment") -> str:
    media = Path(DATA) / f"{new_id()}.mp4"
    media.write_bytes(b"\x00" * 2048)
    jid, cid = new_id(), new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="Stream", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[]))
        s.flush()
        s.add(Clip(id=cid, job_id=jid, kind=kind, status=ClipStatus.READY, title=title,
                   file_path=str(media), file_size=2048, duration=duration, width=w, height=h))
    return cid


def _connect(c) -> str:
    """זרימת OAuth מלאה דרך ה-API ודף האישור של ארגז החול."""
    r = c.post("/api/publish/accounts/sandbox/connect", json={"return_to": "/publishing"}, headers=H)
    assert r.status_code == 200, r.text
    url = r.json()["auth_url"]
    page = c.get(url)
    assert page.status_code == 200 and 'id="approve"' in page.text
    q = parse_qs(urlsplit(url).query)
    r = c.get("/api/publish/sandbox/approve", params={k: v[0] for k, v in q.items()},
              follow_redirects=False)
    assert r.status_code == 303
    cb = r.headers["location"]
    r = c.get(cb.replace("http://testserver", ""), follow_redirects=False)
    assert r.status_code == 303 and "connected=sandbox" in r.headers["location"], r.headers
    accs = c.get("/api/publish/accounts").json()["accounts"]
    return accs[-1]["id"]


def _notifs(kind: str) -> int:
    with session_scope() as s:
        return s.query(Notification).filter(Notification.kind == kind).count()


# --------------------------------------------------------------------------
def test_oauth_state_is_single_use_scoped_and_expiring():
    st, ch = oauth.create_state("sandbox", redirect_uri="x", return_to="//evil.com")
    assert ch and len(st) >= 40
    assert oauth.consume_state(st, "youtube") is None            # פלטפורמה אחרת
    got = oauth.consume_state(st, "sandbox")
    assert got and got["verifier"] and got["return_to"] == "/publishing"   # לא הפניה פתוחה
    assert oauth.consume_state(st, "sandbox") is None            # חד-פעמי
    st2, _ = oauth.create_state("sandbox", redirect_uri="x")
    with session_scope() as s:
        s.get(OAuthState, st2).created_at = datetime.utcnow() - timedelta(minutes=11)
    assert oauth.consume_state(st2, "sandbox") is None            # פג
    v, c = oauth.pkce_pair()
    assert sandbox._challenge(v) == c and 43 <= len(v) <= 128


def test_full_connect_flow_tokens_encrypted_and_never_returned():
    c = _client()
    aid = _connect(c)
    with session_scope() as s:
        a = s.get(SocialAccount, aid)
        assert a.access_token_enc and not a.access_token_enc.startswith("sbx_")
        assert a.status == "connected"
    body = c.get("/api/publish/accounts").text
    assert "sbx_at_" not in body and "sbx_rt_" not in body and "token_enc" not in body
    # state שכבר נוצל – נדחה
    r = c.get("/api/publish/oauth/sandbox/callback?state=nope&code=x", follow_redirects=False)
    assert "publish_error=oauth_state" in r.headers["location"]


def test_pkce_mismatch_and_denial_are_rejected():
    c = _client()
    r = c.post("/api/publish/accounts/sandbox/connect", json={}, headers=H)
    q = parse_qs(urlsplit(r.json()["auth_url"]).query)
    # קוד שהונפק ל-challenge אחר → החלפה נכשלת (PKCE)
    code = sandbox.SandboxProvider.grant("some-other-challenge")
    r = c.get(f"/api/publish/oauth/sandbox/callback?state={q['state'][0]}&code={code}",
              follow_redirects=False)
    assert "publish_error=oauth_failed" in r.headers["location"]
    # ביטול בדף האישור
    r = c.post("/api/publish/accounts/sandbox/connect", json={}, headers=H)
    q = parse_qs(urlsplit(r.json()["auth_url"]).query)
    r = c.get(f"/api/publish/oauth/sandbox/callback?state={q['state'][0]}&error=access_denied",
              follow_redirects=False)
    assert "publish_error=oauth_denied" in r.headers["location"]


def test_sandbox_consent_refuses_foreign_redirects():
    c = _client()
    for bad in ("https://evil.com/api/publish/oauth/sandbox/callback",
                "http://testserver/api/publish/oauth/sandbox/callback?x=1",
                "http://testserver/elsewhere"):
        assert c.get("/api/publish/sandbox/approve", params={"state": "s", "redirect_uri": bad,
                                                             "code_challenge": "c"}).status_code == 400


def test_csrf_header_required_for_changes():
    c = _client()
    assert c.post("/api/publish/accounts/sandbox/connect", json={}).status_code == 403
    assert c.post("/api/publish/jobs", json={"clip_id": "x"}).status_code == 403
    assert c.post("/api/publish/config/youtube", json={"values": {}}).status_code == 403


def test_preflight_catches_problems():
    c = _client()
    aid = _connect(c)
    long_vertical = _clip(duration=400)
    r = c.post("/api/publish/preflight", headers={**H, "X-Polixor-Lang": "en"}, json={
        "clip_id": long_vertical, "mode": "schedule",
        "schedule_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "targets": [{"account_id": aid, "title": "", "privacy": "friends",
                     "description": "x" * 3000}, {"account_id": "nope"}]}).json()
    assert not r["ok"]
    assert [i["key"] for i in r["issues"]] == ["schedule_past"]
    keys = {i["key"] for i in r["targets"][0]["issues"]}
    assert keys >= {"too_long", "title_missing", "privacy_unsupported", "description_too_long"}, keys
    assert r["targets"][1]["issues"][0]["key"] == "account_missing"
    assert "longer than allowed" in r["targets"][0]["issues"][0]["text"] or \
        any("longer" in i["text"] for i in r["targets"][0]["issues"])
    he = c.post("/api/publish/preflight", headers={**H, "X-Polixor-Lang": "he"}, json={
        "clip_id": long_vertical, "targets": [{"account_id": aid, "title": ""}]}).json()
    assert any("כותרת" in i["text"] for i in he["targets"][0]["issues"])
    # יצירה עם בעיות – 422 עם פירוט, ושום דבר לא נוצר
    r = c.post("/api/publish/jobs", headers=H, json={"clip_id": long_vertical,
                                                     "targets": [{"account_id": aid, "title": ""}]})
    assert r.status_code == 422 and r.json()["detail"]["preflight"]["targets"]
    with session_scope() as s:
        assert s.query(PublishJob).filter(PublishJob.clip_id == long_vertical).count() == 0


def test_publish_now_to_two_accounts_and_history():
    c = _client()
    a1, a2 = _connect(c), _connect(c)
    cid = _clip()
    before = len(sandbox.recorded_posts())
    r = c.post("/api/publish/jobs", headers=H, json={"clip_id": cid, "targets": [
        {"account_id": a1, "title": "One", "privacy": "public"},
        {"account_id": a2, "title": "Two", "privacy": "unlisted"}]})
    assert r.status_code == 201, r.text
    assert service.tick(S()) == 2
    h = c.get(f"/api/publish/jobs?clip_id={cid}", headers={"X-Polixor-Lang": "en"}).json()
    assert [i["status"] for i in h["items"]] == ["published", "published"]
    assert h["groups"][0]["title"] == "Published"
    assert all(i["remote_url"].startswith("https://sandbox.invalid/") for i in h["items"])
    assert len(sandbox.recorded_posts()) == before + 2
    assert _notifs("publish_complete") >= 2


def test_platform_scheduling_uploads_now():
    c = _client()
    aid = _connect(c)
    cid = _clip()
    real = registry.get
    registry.register(sandbox.SandboxProvider(native_scheduling=True))
    try:
        when = datetime.now(timezone.utc) + timedelta(days=1)
        r = c.post("/api/publish/preflight", headers=H, json={
            "clip_id": cid, "mode": "schedule", "schedule_at": when.isoformat(),
            "targets": [{"account_id": aid, "title": "Later"}]}).json()
        assert r["targets"][0]["schedule_by"] == "platform"
        assert r["targets"][0]["needs_server_online"] is False
        c.post("/api/publish/jobs", headers=H, json={
            "clip_id": cid, "mode": "schedule", "schedule_at": when.isoformat(),
            "targets": [{"account_id": aid, "title": "Later"}]})
        service.tick(S())
        j = c.get(f"/api/publish/jobs?clip_id={cid}").json()["items"][0]
        assert j["status"] == "scheduled_on_platform" and j["can_cancel"] is False
        post = sandbox.recorded_posts()[-1]
        assert post["publish_at"] and post["title"] == "Later"
    finally:
        registry.unregister("sandbox")
    assert real is registry.get


def test_polixor_scheduling_waits_and_warns_server_online():
    c = _client()
    aid = _connect(c)
    cid = _clip()
    when = datetime.now(timezone.utc) + timedelta(hours=2)
    r = c.post("/api/publish/jobs", headers=H, json={
        "clip_id": cid, "mode": "schedule", "schedule_at": when.isoformat(),
        "targets": [{"account_id": aid, "title": "At 2"}]})
    jid = r.json()["jobs"][0]
    service.tick(S())
    j = c.get(f"/api/publish/jobs?clip_id={cid}").json()["items"][0]
    assert j["status"] == "scheduled" and j["needs_server_online"] is True
    assert _notifs("publish_scheduled") >= 1
    # הזמן הגיע
    with session_scope() as s:
        s.get(PublishJob, jid).schedule_at = datetime.utcnow() - timedelta(minutes=1)
    service.tick(S())
    with session_scope() as s:
        assert s.get(PublishJob, jid).status == "published"


def test_missed_schedule_fails_with_notification_and_can_retry():
    c = _client()
    aid = _connect(c)
    cid = _clip()
    r = c.post("/api/publish/jobs", headers=H, json={
        "clip_id": cid, "mode": "schedule",
        "schedule_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "targets": [{"account_id": aid, "title": "Missed"}]})
    jid = r.json()["jobs"][0]
    with session_scope() as s:
        s.get(PublishJob, jid).schedule_at = datetime.utcnow() - timedelta(hours=10)
    n = _notifs("schedule_failed")
    service.tick(S(publish_missed_grace_minutes=60))
    with session_scope() as s:
        j = s.get(PublishJob, jid)
        assert j.status == "failed" and j.error["key"] == "publishing.error.missed"
    assert _notifs("schedule_failed") == n + 1
    assert c.post(f"/api/publish/jobs/{jid}/retry", headers=H).status_code == 200
    service.tick(S())
    with session_scope() as s:
        assert s.get(PublishJob, jid).status == "published"


def test_temporary_errors_retry_with_backoff():
    c = _client()
    aid = _connect(c)
    cid = _clip(title="retry me")
    r = c.post("/api/publish/jobs", headers=H, json={"clip_id": cid, "targets": [
        {"account_id": aid, "title": "Flaky", "options": {"sandbox_simulate": "retry"}}]})
    jid = r.json()["jobs"][0]
    service.tick(S())
    with session_scope() as s:
        j = s.get(PublishJob, jid)
        assert j.status == "queued" and j.attempts == 1 and j.next_attempt_at > datetime.utcnow()
    assert service.tick(S()) == 0                                  # עוד לא הגיע הזמן
    with session_scope() as s:
        s.get(PublishJob, jid).next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
    service.tick(S())
    with session_scope() as s:
        assert s.get(PublishJob, jid).status == "published"


def test_permanent_failure_notifies_and_is_translated():
    c = _client()
    aid = _connect(c)
    cid = _clip()
    r = c.post("/api/publish/jobs", headers=H, json={"clip_id": cid, "targets": [
        {"account_id": aid, "title": "Bad", "options": {"sandbox_simulate": "fail"}}]})
    n = _notifs("publish_failed")
    service.tick(S())
    items = c.get(f"/api/publish/jobs?clip_id={cid}", headers={"X-Polixor-Lang": "en"}).json()
    j = items["items"][0]
    assert j["status"] == "failed" and "rejected" in j["error"] and j["can_retry"]
    assert items["groups"][0]["state"] == "needs_attention"
    he = c.get(f"/api/publish/jobs?clip_id={cid}", headers={"X-Polixor-Lang": "he"}).json()
    assert "דחתה" in he["items"][0]["error"]
    assert _notifs("publish_failed") == n + 1
    assert r.status_code == 201
    # ההתראה כוללת את הסיבה, בשפת הצפייה
    from polixor.services import notifications as N
    with i18n.use_lang("en"):
        body = [x for x in N.listing()["items"] if x["kind"] == "publish_failed"][0]["body"]
    assert "rejected the video" in body, body
    with i18n.use_lang("he"):
        body = [x for x in N.listing()["items"] if x["kind"] == "publish_failed"][0]["body"]
    assert "דחתה" in body, body


def test_expired_token_is_refreshed_and_revoked_one_needs_reconnect():
    c = _client()
    aid = _connect(c)
    with session_scope() as s:
        s.get(SocialAccount, aid).token_expires_at = datetime.utcnow() - timedelta(minutes=5)
    cid = _clip()
    c.post("/api/publish/jobs", headers=H, json={"clip_id": cid,
                                                 "targets": [{"account_id": aid, "title": "R"}]})
    service.tick(S())
    with session_scope() as s:
        a = s.get(SocialAccount, aid)
        assert a.status == "connected" and a.token_expires_at > datetime.utcnow()
    # טוקן בוטל אצל הפלטפורמה (ארגז החול שוכח את כל הטוקנים)
    sandbox.reset_for_tests(access_tokens=[])
    cid2 = _clip()
    n = _notifs("account_reconnect")
    c.post("/api/publish/jobs", headers=H, json={"clip_id": cid2,
                                                 "targets": [{"account_id": aid, "title": "X"}]})
    service.tick(S())
    with session_scope() as s:
        assert s.get(SocialAccount, aid).status == "reconnect_required"
        j = s.query(PublishJob).filter(PublishJob.clip_id == cid2).one()
        assert j.status == "failed" and j.error["key"] == "publishing.error.reconnect"
    assert _notifs("account_reconnect") == n + 1
    pf = c.post("/api/publish/preflight", headers=H, json={
        "clip_id": cid2, "targets": [{"account_id": aid, "title": "X"}]}).json()
    assert pf["targets"][0]["issues"][0]["key"] == "reconnect"


def test_interrupted_upload_is_not_resent():
    c = _client()
    aid = _connect(c)
    cid = _clip()
    jid = c.post("/api/publish/jobs", headers=H, json={
        "clip_id": cid, "targets": [{"account_id": aid, "title": "Crash"}]}).json()["jobs"][0]
    with session_scope() as s:
        j = s.get(PublishJob, jid)
        j.status, j.locked_until = "uploading", datetime.utcnow() - timedelta(minutes=1)
    before = len(sandbox.recorded_posts())
    service.tick(S())
    with session_scope() as s:
        j = s.get(PublishJob, jid)
        assert j.status == "failed" and j.error["key"] == "publishing.error.interrupted"
    assert len(sandbox.recorded_posts()) == before                 # לא נשלח שוב


def test_lease_prevents_double_execution():
    c = _client()
    aid = _connect(c)
    cid = _clip()
    jid = c.post("/api/publish/jobs", headers=H, json={
        "clip_id": cid, "targets": [{"account_id": aid, "title": "Once"}]}).json()["jobs"][0]
    assert service._claim(jid)
    assert service.run(jid, S()) == "locked"
    service._release(jid)
    assert service.run(jid, S()) == "published"
    assert service.run(jid, S()) == "published"                    # לא מפרסם שוב


def test_cancel_and_disconnect():
    c = _client()
    aid = _connect(c)
    cid = _clip()
    when = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    ids = c.post("/api/publish/jobs", headers=H, json={
        "clip_id": cid, "mode": "schedule", "schedule_at": when,
        "targets": [{"account_id": aid, "title": "A"}]}).json()["jobs"]
    assert c.post(f"/api/publish/jobs/{ids[0]}/cancel", headers=H).status_code == 200
    assert c.post(f"/api/publish/jobs/{ids[0]}/retry", headers=H).status_code == 409
    ids = c.post("/api/publish/jobs", headers=H, json={
        "clip_id": cid, "mode": "schedule", "schedule_at": when,
        "targets": [{"account_id": aid, "title": "B"}]}).json()["jobs"]
    assert c.post(f"/api/publish/accounts/{aid}/disconnect", headers=H).json() == {"disconnected": True}
    with session_scope() as s:
        assert s.get(SocialAccount, aid) is None
        assert s.get(PublishJob, ids[0]).status == "cancelled"


def test_schedule_time_requires_timezone():
    c = _client()
    aid = _connect(c)
    cid = _clip()
    r = c.post("/api/publish/jobs", headers=H, json={
        "clip_id": cid, "mode": "schedule", "schedule_at": "2030-01-01T10:00:00",
        "targets": [{"account_id": aid, "title": "T"}]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "timezone_required"


def test_long_video_uses_the_same_system():
    c = _client()
    aid = _connect(c)
    cid = _clip(kind=ClipKind.LONG, duration=1200, w=1920, h=1080)
    pf = c.post("/api/publish/preflight", headers=H, json={
        "clip_id": cid, "targets": [{"account_id": aid, "title": "Full stream"}]}).json()
    assert pf["ok"] and pf["format"] == "long"
    c.post("/api/publish/jobs", headers=H, json={
        "clip_id": cid, "targets": [{"account_id": aid, "title": "Full stream"}]})
    service.tick(S())
    assert sandbox.recorded_posts()[-1]["format"] == "long"


def test_platforms_and_developer_config_are_masked():
    c = _client()
    p = c.get("/api/publish/platforms").json()
    ids = {x["id"]: x for x in p["platforms"]}
    assert set(ids) >= {"youtube", "instagram", "facebook", "tiktok", "sandbox"}
    assert ids["tiktok"]["available"] is False and ids["tiktok"]["planned_stage"] == 9
    assert ids["youtube"]["available"] is True                     # שלב 7
    assert ids["sandbox"]["available"] is True and "scheduler" in p
    secret = "super-secret-value-123456"
    r = c.post("/api/publish/config/youtube", headers=H,
               json={"values": {"youtube_client_id": "abc.apps.googleusercontent.com",
                                "youtube_client_secret": secret, "not_a_field": "x"}})
    assert r.status_code == 200
    cfg = c.get("/api/publish/config").text
    assert secret not in cfg and "3456" in cfg
    g = [x for x in c.get("/api/publish/config").json()["groups"] if x["id"] == "youtube"][0]
    assert g["configured"] and g["redirect_uris"][0].endswith("/api/publish/oauth/youtube/callback")
    assert not SECRETS.has("not_a_field")
    assert c.post("/api/publish/config/youtube", headers=H,
                  json={"values": {"youtube_client_id": "a b"}}).status_code == 400
    for f in ("youtube_client_id", "youtube_client_secret"):
        SECRETS.delete(f)
    # פלטפורמה שעוד לא זמינה – חיבור נדחה בהודעה ברורה
    r = c.post("/api/publish/accounts/tiktok/connect", json={}, headers={**H, "X-Polixor-Lang": "en"})
    assert r.status_code == 400 and "isn't available yet" in r.json()["detail"]["message"]
    # YouTube זמין אבל בלי פרטי אפליקציה – הודעה ברורה איפה להוסיף אותם
    r = c.post("/api/publish/accounts/youtube/connect", json={}, headers={**H, "X-Polixor-Lang": "en"})
    assert r.status_code == 400 and "developer app details are missing" in r.json()["detail"]["message"]


def test_sandbox_off_hides_everything():
    c = _client()
    SETTINGS.update({"publish_sandbox": False})
    try:
        assert c.get("/api/publish/sandbox/posts").status_code == 404
        ids = {x["id"] for x in c.get("/api/publish/platforms").json()["platforms"]}
        assert "sandbox" not in ids
    finally:
        SETTINGS.update({"publish_sandbox": True})


def test_every_status_and_issue_is_translated():
    import re

    from polixor.locales import publishing as L
    for key, v in L.MESSAGES.items():
        assert v.get("he") and v.get("en"), key
        assert set(re.findall(r"{(\w+)}", v["he"])) == set(re.findall(r"{(\w+)}", v["en"])), key
    for st in ("queued", "scheduled", "uploading", "processing", "scheduled_on_platform",
               "published", "failed", "cancelled"):
        assert i18n.has(f"publishing.status.{st}")


# --------------------------------------------------------------------------
def _run_all() -> int:
    order = [n for n in globals() if n.startswith("test_")]
    fns = [(n, globals()[n]) for n in order]
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
    print(f"\n{passed}/{len(fns)} בדיקות תשתית פרסום עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
