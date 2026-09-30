"""
בדיקות למטא-דאטה לרשתות (שלב 10): כותרת, כיתוב והאשטגים לכל פלטפורמה מתוך
התמלול האמיתי של הקליפ. מודל השפה מדומה (llm.call_model) – שום קריאה אמיתית.

  * בלי מודל: הצעה לפי כללים, רק ממילים שבתמלול, עם הערה מתורגמת.
  * עם מודל: גרסה לכל פלטפורמה; מגבלות אורך; נרמול וסינון כפילויות
    בהאשטגים; בלי כותרת ל-Instagram/TikTok; תשובה שבורה → נפילה לכללים.
  * ההקשר: רק התמלול שבתוך הקליפ, שפת התוכן, הוו והפאנץ'.
  * שמירה במטמון לפי טביעה; "שוב" מייצר מחדש; שינוי תמלול מבטל את המטמון.
  * API: CSRF, 404 לקליפ חסר, הערות בעברית ובאנגלית.
  * בדיקה מוקדמת: כיתוב (בלי כותרת) מספיק לפלטפורמות של כיתוב בלבד.

הרצה:  python3 tests/test_social_metadata.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = tempfile.mkdtemp(prefix="pxmeta_")
os.environ["POLIXOR_DATA_DIR"] = DATA
os.environ["POLIXOR_SCHEDULER"] = "0"

from polixor import i18n                                          # noqa: E402
from polixor.config import PATHS, SETTINGS                        # noqa: E402
from polixor.db import init_db, session_scope                     # noqa: E402
from polixor.models import Clip, ClipKind, ClipStatus, Job, JobStatus, new_id  # noqa: E402
from polixor.services import analysis_store, llm, social_metadata as sm  # noqa: E402

PATHS.ensure()
init_db()
SETTINGS.update({"publish_sandbox": True})
H = {"X-Polixor-Request": "1"}

HE_SEGMENTS = [
    (0.0, 8.0, "שלום לכולם וברוכים הבאים לשידור."),
    (10.0, 16.0, "היום אני מראה לכם איך לבשל שקשוקה מושלמת בעשר דקות."),
    (16.0, 22.0, "הסוד של השקשוקה הוא עגבניות טריות ופלפל חריף."),
    (22.0, 28.0, "ככה השקשוקה יוצאת עשירה וטעימה בכל פעם."),
    (40.0, 50.0, "ועכשיו נעבור לשאלות מהצ'אט על מסעדות בתל אביב."),
]
EN_SEGMENTS = [
    (0.0, 6.0, "Welcome back to the stream everyone."),
    (6.0, 12.0, "This keyboard trick saves me an hour every single day."),
    (12.0, 18.0, "You press control and shift and the keyboard does the rest."),
]


def _job(segments, language="he") -> tuple[str, Path]:
    jid = new_id()
    work = Path(DATA) / jid
    work.mkdir(parents=True)
    tp = work / "transcript.json"
    tp.write_text(json.dumps({"language": language, "segments": [
        {"start": a, "end": b, "text": t, "language": language} for a, b, t in segments]},
        ensure_ascii=False), "utf-8")
    with session_scope() as s:
        s.add(Job(id=jid, title="Live", status=JobStatus.COMPLETED, content_language=language,
                  artifacts={"transcript_path": str(tp)}, completed_stages=[]))
    return jid, work


def _clip(jid: str, start: float, end: float, title: str = "", w=1080, h=1920) -> str:
    media = Path(DATA) / f"{new_id()}.mp4"
    media.write_bytes(b"\x00" * 2048)
    cid = new_id()
    with session_scope() as s:
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title=title,
                   file_path=str(media), file_size=2048, duration=end - start, width=w, height=h,
                   source_start=start, source_end=end))
    return cid


class FakeLLM:
    """מחליף את llm.call_model / is_llm_enabled ורושם מה נשלח."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []

    def __enter__(self):
        self._saved = (llm.call_model, llm.is_llm_enabled)
        llm.is_llm_enabled = lambda settings: True

        def call(system, user, settings):
            self.calls.append({"system": system, "user": json.loads(user)})
            return self.reply(json.loads(user)) if callable(self.reply) else self.reply
        llm.call_model = call
        return self

    def __exit__(self, *exc):
        llm.call_model, llm.is_llm_enabled = self._saved


def _no_llm():
    saved = llm.is_llm_enabled
    llm.is_llm_enabled = lambda settings: False
    return saved


def _client():
    from fastapi.testclient import TestClient

    from polixor.main import app
    return TestClient(app, base_url="http://testserver")


# --------------------------------------------------------------------------
def test_context_uses_only_the_clip_transcript_and_review():
    jid, work = _job(HE_SEGMENTS)
    cid = _clip(jid, 10.0, 28.0, title="שקשוקה בעשר דקות")
    rp = analysis_store.save_clip_review(work, {"selected": [
        {"start": 10.0, "end": 28.0, "hook": {"text": "איך לבשל שקשוקה מושלמת"},
         "payoff": {"text": "יוצאת עשירה וטעימה"}}]})
    with session_scope() as s:
        job = s.get(Job, jid)
        job.artifacts = {**job.artifacts, "clip_review_path": str(rp)}
    ctx = sm.clip_context(cid)
    assert "שקשוקה" in ctx["transcript"]
    assert "ברוכים הבאים" not in ctx["transcript"] and "מסעדות" not in ctx["transcript"]
    assert ctx["language"] == "he" and ctx["format"] == "short"
    assert ctx["hook"] == "איך לבשל שקשוקה מושלמת" and ctx["payoff"] == "יוצאת עשירה וטעימה"
    assert sm.clip_context("nope") is None


def test_rules_fallback_uses_only_transcript_words():
    saved = _no_llm()
    try:
        jid, _ = _job(HE_SEGMENTS)
        cid = _clip(jid, 10.0, 28.0)
        with i18n.use_lang("he"):
            out = sm.generate(cid, ["youtube", "instagram", "tiktok"], SETTINGS.get())
        assert out["source"] == "rules" and out["language"] == "he"
        assert "מודל שפה" in out["note"]
        ctx_words = set(sm._WORD.findall(sm.clip_context(cid)["transcript"]))
        for p, v in out["platforms"].items():
            assert v["hashtags"], p
            for tag in v["hashtags"]:
                assert tag in ctx_words, (p, tag)          # שום האשטג שלא נאמר בקליפ
            assert "מסעדות" not in v["text"] and "מסעדות" not in v["title"]
        assert "שקשוקה" in out["platforms"]["youtube"]["title"]
        assert out["platforms"]["instagram"]["title"] == ""       # כיתוב בלבד
        assert out["platforms"]["tiktok"]["title"] == ""
        assert "שקשוקה" in out["platforms"]["instagram"]["text"]
        # "השקשוקה" ו"שקשוקה" נספרות יחד → הנושא האמיתי ראשון; בלי מילות מילוי
        assert out["platforms"]["youtube"]["hashtags"][0] == "שקשוקה"
        for filler in ("היום", "לכם", "אני"):
            assert filler not in out["platforms"]["youtube"]["hashtags"]
    finally:
        llm.is_llm_enabled = saved


def test_rules_fallback_english():
    saved = _no_llm()
    try:
        jid, _ = _job(EN_SEGMENTS, language="en")
        cid = _clip(jid, 6.0, 18.0, title="Keyboard trick")
        with i18n.use_lang("en"):
            out = sm.generate(cid, ["youtube", "facebook"], SETTINGS.get())
        assert out["language"] == "en" and "No language model" in out["note"]
        assert out["platforms"]["youtube"]["title"] == "Keyboard trick"
        assert "keyboard" in [t.lower() for t in out["platforms"]["youtube"]["hashtags"]]
        assert len(out["platforms"]["facebook"]["hashtags"]) <= sm.RULES["facebook"]["tags"][1]
        for t in out["platforms"]["youtube"]["hashtags"]:
            assert t.lower() not in sm._STOP["en"]
    finally:
        llm.is_llm_enabled = saved


def test_ai_variants_are_cleaned_and_limited():
    jid, _ = _job(HE_SEGMENTS)
    cid = _clip(jid, 10.0, 28.0, title="שקשוקה")
    long_title = "שקשוקה " * 40
    reply = json.dumps({"platforms": {
        "youtube": {"title": long_title, "text": "המתכון המלא לשקשוקה #שקשוקה #אוכל",
                    "hashtags": ["#שקשוקה", "שקשוקה", "אוכל ישראלי", "#Food!", "a-b", "", "x1", "x2",
                                 "x3", "x4", "x5"]},
        "instagram": {"title": "לא אמור להופיע", "caption": "שקשוקה בעשר דקות 🍳",
                      "hashtags": ["שקשוקה", "מתכון"]},
        "tiktok": {"title": "", "text": "א" * 5000, "hashtags": ["שקשוקה"]},
    }}, ensure_ascii=False)
    with FakeLLM(reply) as fake:
        out = sm.generate(cid, ["youtube", "instagram", "tiktok"], SETTINGS.get(), regenerate=True)
    assert out["source"] == "ai" and "note" not in out
    sent = fake.calls[0]["user"]
    assert sent["language"] == "he" and sent["platforms"] == ["youtube", "instagram", "tiktok"]
    assert "שקשוקה" in sent["transcript"] and "מסעדות" not in sent["transcript"]
    assert sent["rules"]["instagram"]["title_max"] == 0
    assert "Never invent" in fake.calls[0]["system"]
    yt = out["platforms"]["youtube"]
    assert len(yt["title"]) <= 100
    assert yt["text"] == "המתכון המלא לשקשוקה"                  # האשטגים בסוף הטקסט הוסרו
    assert yt["hashtags"][:4] == ["שקשוקה", "אוכלישראלי", "Food", "ab"]
    assert len(yt["hashtags"]) == 8                            # כפילות וריק סוננו; תקרה 8
    assert out["platforms"]["instagram"]["title"] == ""
    assert out["platforms"]["instagram"]["text"] == "שקשוקה בעשר דקות 🍳"
    tt = out["platforms"]["tiktok"]
    assert len(tt["text"]) + sum(len(t) + 2 for t in tt["hashtags"]) <= 2200
    assert tt["text"].endswith("…")


def test_bad_ai_reply_falls_back_to_rules_with_note():
    jid, _ = _job(HE_SEGMENTS)
    cid = _clip(jid, 10.0, 28.0)
    for reply in ("not json at all", json.dumps({"platforms": {"youtube": {"title": "x"}}})):
        with FakeLLM(reply), i18n.use_lang("en"):
            out = sm.generate(cid, ["youtube", "instagram"], SETTINGS.get(), regenerate=True)
        assert out["source"] == "rules", reply
        assert "didn't return a valid suggestion" in out["note"]
        assert out["platforms"]["youtube"]["hashtags"]


def test_cache_regenerate_and_invalidation():
    jid, _ = _job(EN_SEGMENTS, language="en")
    cid = _clip(jid, 6.0, 18.0, title="Keyboard trick")
    n = {"i": 0}

    def reply(user):
        n["i"] += 1
        return json.dumps({"platforms": {p: {"title": f"T{n['i']}", "text": "keyboard trick",
                                             "hashtags": ["keyboard"]} for p in user["platforms"]}})
    with FakeLLM(reply) as fake:
        a = sm.generate(cid, ["youtube"], SETTINGS.get())
        b = sm.generate(cid, ["youtube"], SETTINGS.get())
        assert len(fake.calls) == 1 and b["cached"] and b["platforms"] == a["platforms"]
        c = sm.generate(cid, ["youtube", "facebook"], SETTINGS.get())    # פלטפורמה חדשה → קריאה
        assert len(fake.calls) == 2 and not c["cached"]
        d = sm.generate(cid, ["youtube", "facebook"], SETTINGS.get())
        assert d["cached"] and len(fake.calls) == 2
        e = sm.generate(cid, ["youtube"], SETTINGS.get(), regenerate=True)
        assert len(fake.calls) == 3 and e["platforms"]["youtube"]["title"] == "T3"
        with session_scope() as s:                             # עריכת כותרת הקליפ → טביעה חדשה
            s.get(Clip, cid).title = "Another title"
        f = sm.generate(cid, ["youtube"], SETTINGS.get())
        assert len(fake.calls) == 4 and not f["cached"]
    with session_scope() as s:
        cache = s.get(Clip, cid).render_params["social_metadata"]
    assert cache["version"] == sm.VERSION and cache["source"] == "ai"
    assert set(cache["platforms"]) == {"youtube"}             # שאר הפלטפורמות של הטביעה הישנה נמחקו


def test_no_transcript_note_and_unknown_platforms():
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="x", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[]))
    cid = _clip(jid, 0.0, 10.0, title="Only a title")
    with FakeLLM("{}") as fake, i18n.use_lang("he"):
        out = sm.generate(cid, ["myspace"], SETTINGS.get())
    assert not fake.calls                                      # אין תמלול → לא פונים למודל
    assert set(out["platforms"]) == set(sm.PLATFORMS)          # פלטפורמה לא מוכרת → ברירת מחדל
    assert out["note"] == i18n.tr("social_metadata.no_transcript", "he")
    assert out["platforms"]["youtube"]["title"] == "Only a title"
    try:
        sm.generate("missing", ["youtube"], SETTINGS.get())
        raise AssertionError("expected LookupError")
    except LookupError:
        pass


def test_hashtag_normalisation():
    assert sm.normalize_hashtag("#Hello World") == "HelloWorld"
    assert sm.normalize_hashtag("##שלום-עולם!") == "שלוםעולם"
    assert sm.normalize_hashtag("   ") == ""
    assert len(sm.normalize_hashtag("a" * 80)) == 50


def test_api_endpoint_csrf_404_and_languages():
    saved = _no_llm()
    try:
        jid, _ = _job(HE_SEGMENTS)
        cid = _clip(jid, 10.0, 28.0)
        with _client() as c:
            r = c.post("/api/publish/metadata", json={"clip_id": cid, "platforms": ["youtube"]})
            assert r.status_code == 403
            r = c.post("/api/publish/metadata", json={"clip_id": "nope", "platforms": ["youtube"]},
                       headers=H)
            assert r.status_code == 404
            r = c.post("/api/publish/metadata", json={"clip_id": cid, "platforms": ["youtube", "tiktok"]},
                       headers={**H, "Accept-Language": "he"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert set(body["platforms"]) == {"youtube", "tiktok"} and body["source"] == "rules"
            assert "מודל שפה" in body["note"]
            r = c.post("/api/publish/metadata", json={"clip_id": cid, "platforms": ["youtube"],
                                                       "regenerate": True},
                       headers={**H, "Accept-Language": "en"})
            assert "No language model" in r.json()["note"]
    finally:
        llm.is_llm_enabled = saved


def test_preflight_caption_is_enough_for_caption_only_platforms():
    from polixor.models import SocialAccount
    from polixor.services.publishing import registry, service
    from polixor.services.publishing.base import Capabilities
    from polixor.services.publishing.sandbox import SandboxProvider

    class CaptionOnly(SandboxProvider):
        def capabilities(self) -> Capabilities:
            cap = super().capabilities()
            return Capabilities(**{**cap.__dict__, "notes": ("sandbox", "caption_combined")})

    jid, _ = _job(HE_SEGMENTS)
    cid = _clip(jid, 10.0, 28.0)
    aid = new_id()
    with session_scope() as s:
        s.add(SocialAccount(id=aid, platform="sandbox", external_id=new_id(), display_name="t",
                            status="connected"))

    def issues(title, desc):
        res = service.preflight(cid, [{"account_id": aid, "title": title, "description": desc,
                                       "privacy": "public", "format": "short"}], SETTINGS.get())
        return {i["key"] for t in res["targets"] for i in t["issues"]}

    assert "title_missing" in issues("", "some caption")      # פלטפורמה עם כותרת
    registry.register(CaptionOnly())
    try:
        got = issues("", "some caption")
        assert "title_missing" not in got and "caption_missing" not in got, got
        assert "caption_missing" in issues("", "  ")
    finally:
        registry.unregister("sandbox")
    assert i18n.tr("publishing.issue.caption_missing", "he") and \
        i18n.tr("publishing.issue.caption_missing", "en")


def test_locale_parity():
    import re

    from polixor.locales import social_metadata as L
    for key, v in L.MESSAGES.items():
        assert v.get("he") and v.get("en"), key
        assert set(re.findall(r"{(\w+)}", v["he"])) == set(re.findall(r"{(\w+)}", v["en"])), key


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
    print(f"\n{passed}/{len(fns)} בדיקות מטא-דאטה לרשתות עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
