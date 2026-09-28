"""
בדיקות יחידה לשני הפיצ'רים החדשים: AI Images וקליטת שידור חי.

הבדיקות כאן אינן נוגעות ברשת: ספק התמונות ופותר הזרם מוזרקים
כפונקציות מזויפות, וכך נבדקים אימות קלט, מסלולי כשל, מכונת המצבים
ושמירת חומר חלקי – בלי תלות בשירות חיצוני.

הרצה:  python3 tests/test_images_live.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os                                                            # noqa: E402

_TMP = tempfile.mkdtemp(prefix="pxtest_")
os.environ.setdefault("POLIXOR_DATA_DIR", _TMP)

from polixor.config import PATHS, AppSettings                        # noqa: E402

PATHS.ensure()

from polixor.db import init_db, session_scope                        # noqa: E402

init_db()

from polixor.errors import PolixorError                              # noqa: E402
from polixor.models import (                                         # noqa: E402
    COMPOSITE_ROLES, TIMELINE_ROLES, Clip, ClipKind, ClipStatus,
    GeneratedImage, ImagePlacement, ImageRole, ImageStatus, Job, JobStatus,
    LiveState, SourceKind, live_can_transition, new_id,
)
from polixor.services import image_assets, image_render, images, live  # noqa: E402
from polixor.services import ingest, visual_suggest                  # noqa: E402
from polixor.services.live_capture import (                          # noqa: E402
    CaptureConfig, StateMachine, run_capture,
)
from polixor.services.live import SegmentResult                      # noqa: E402


def _settings(**over) -> AppSettings:
    base = AppSettings().to_dict()
    base.update({"image_provider": "placeholder", "image_retries": 0})
    base.update(over)
    return AppSettings.from_dict(base)


def _make_clip(duration: float = 20.0, aspect: str = "9:16") -> str:
    job_id, clip_id = new_id(), new_id()
    with session_scope() as s:
        s.add(Job(id=job_id, title="בדיקה", status=JobStatus.COMPLETED))
        s.add(Clip(id=clip_id, job_id=job_id, kind=ClipKind.SHORT,
                   status=ClipStatus.READY, title="קליפ בדיקה",
                   aspect=aspect, duration=duration))
    return clip_id


def _make_image(*, ready: bool = True, job_id: str | None = None) -> str:
    image_id = new_id()
    path = PATHS.images / f"{image_id}.png"
    if ready:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048)
    with session_scope() as s:
        s.add(GeneratedImage(
            id=image_id, job_id=job_id, prompt="test prompt here",
            aspect="9:16",
            status=ImageStatus.READY if ready else ImageStatus.FAILED,
            file_path=str(path) if ready else "",
            width=864, height=1536, is_ai=False,
        ))
    return image_id


# ==========================================================================
# 1. אימות בקשת יצירת תמונה
# ==========================================================================
def test_image_prompt_validation_rejects_bad_input():
    for bad in ("", "  ", "ab", "x" * 5000):
        try:
            images.validate_prompt(bad)
            raise AssertionError(f"פרומפט פסול התקבל: {bad[:20]!r}")
        except images.ImagePromptError as exc:
            assert exc.code == "image_bad_prompt"
            assert exc.message and exc.hint

    clean = images.validate_prompt("  a   cinematic  dark  forest  ")
    assert clean == "a cinematic dark forest", clean


def test_image_aspect_validation():
    for good in ("1:1", "16:9", "9:16"):
        assert images.validate_aspect(good) == good
    for bad in ("4:3", "", "16x9", "9:16 "):
        try:
            images.validate_aspect(bad)
            raise AssertionError(f"יחס פסול התקבל: {bad!r}")
        except images.ImagePromptError:
            pass

    assert images.aspect_of(1024, 1024) == "1:1"
    assert images.aspect_of(1536, 1024) == "16:9"
    assert images.aspect_of(864, 1536) == "9:16"


# ==========================================================================
# 2. מפתח API חסר
# ==========================================================================
def test_missing_api_key_is_reported_not_faked():
    provider = images.OpenAIImageProvider()
    available, reason = provider.available()
    # בסביבת הבדיקה אין מפתח
    if not available:
        assert "מפתח" in reason, reason
        try:
            provider.generate("a valid prompt", aspect="16:9",
                              settings=_settings(image_provider="openai"))
            raise AssertionError("יצירה ללא מפתח לא אמורה להצליח")
        except images.ImageKeyMissingError as exc:
            assert exc.code == "image_key_missing"
            assert "הגדרות" in exc.hint


def test_provider_status_marks_non_ai_provider():
    rows = images.provider_status(_settings())
    by_name = {r["name"]: r for r in rows}
    assert by_name["openai"]["is_ai"] is True
    # ספק מקומי חייב להיות מסומן במפורש כלא-AI
    assert by_name["placeholder"]["is_ai"] is False
    assert not images.provider_is_ai("placeholder")
    assert images.provider_is_ai("openai")


# ==========================================================================
# 3. כשל בצד השרת
# ==========================================================================
def test_server_failure_does_not_retry_unretryable_errors():
    calls = {"n": 0}

    class Failing(images.ImageProvider):
        name, label, is_ai = "failing", "בדיקה", True

        def available(self):
            return True, ""

        def generate(self, prompt, *, aspect, settings, on_progress=None,
                     cancel_event=None):
            calls["n"] += 1
            raise images.ImageRejectedError("נדחה על-ידי מדיניות התוכן.")

        def vary(self, prompt, source, *, aspect, settings, on_progress=None,
                 cancel_event=None):
            return self.generate(prompt, aspect=aspect, settings=settings)

    images.PROVIDERS["failing"] = Failing
    try:
        try:
            images.generate_image("a valid prompt", aspect="16:9",
                                  settings=_settings(image_retries=3),
                                  provider_name="failing")
            raise AssertionError("היה אמור להיכשל")
        except images.ImageRejectedError:
            pass
        # דחיית מדיניות אינה תקלה זמנית: אסור לנסות שוב
        assert calls["n"] == 1, f"ניסיונות: {calls['n']}"
    finally:
        images.PROVIDERS.pop("failing", None)


def test_transient_failure_is_retried():
    calls = {"n": 0}

    class Flaky(images.ImageProvider):
        name, label, is_ai = "flaky", "בדיקה", True

        def available(self):
            return True, ""

        def generate(self, prompt, *, aspect, settings, on_progress=None,
                     cancel_event=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise images.ImageRateLimitError()
            return images.GeneratedImageData(
                data=b"ok", width=10, height=10, provider="flaky",
                model="t", is_ai=True)

    images.PROVIDERS["flaky"] = Flaky
    try:
        st = _settings(image_retries=3)
        # קיצור ההמתנה כדי שהבדיקה לא תיתקע
        original = images.time.sleep
        images.time.sleep = lambda *_: None
        try:
            out = images.generate_image("a valid prompt", aspect="16:9",
                                        settings=st, provider_name="flaky")
        finally:
            images.time.sleep = original
        assert out.data == b"ok"
        assert calls["n"] == 3, calls["n"]
    finally:
        images.PROVIDERS.pop("flaky", None)


def test_cancelled_request_stops_generation():
    cancel = threading.Event()
    cancel.set()
    try:
        images.generate_image("a valid prompt", aspect="16:9",
                              settings=_settings(), cancel_event=cancel)
        raise AssertionError("בקשה מבוטלת הייתה אמורה להיעצר")
    except images.ImageCancelledError as exc:
        assert exc.code == "image_cancelled"


# ==========================================================================
# 4. שמירה בפרויקט
# ==========================================================================
def test_image_persists_in_project():
    job_id = new_id()
    with session_scope() as s:
        s.add(Job(id=job_id, title="פרויקט", status=JobStatus.COMPLETED))

    image_id = image_assets.create_image_row(
        prompt="a cinematic dark forest at night", aspect="9:16",
        job_id=job_id, settings=_settings())

    with session_scope() as s:
        row = s.get(GeneratedImage, image_id)
        assert row is not None
        assert row.job_id == job_id
        assert row.status == ImageStatus.QUEUED
        assert row.aspect == "9:16"
        # ספק מקומי נרשם מראש כלא-AI
        assert row.is_ai is False

    # יצירה בפועל מול הספק המקומי, מקצה לקצה
    image_assets.start_generation(image_id, settings=_settings())
    for _ in range(80):
        with session_scope() as s:
            row = s.get(GeneratedImage, image_id)
            if row.status in (ImageStatus.READY, ImageStatus.FAILED):
                break
        threading.Event().wait(0.25)

    with session_scope() as s:
        row = s.get(GeneratedImage, image_id)
        assert row.status == ImageStatus.READY, row.error
        assert Path(row.file_path).is_file()
        assert row.width > 0 and row.height > 0
        assert row.is_ai is False and row.note


# ==========================================================================
# 5. שיבוץ בתכנית העריכה
# ==========================================================================
def test_placement_roles_split_correctly():
    assert TIMELINE_ROLES == {"intro", "outro", "insert"}
    assert COMPOSITE_ROLES == {"broll", "overlay", "background"}
    # תמונת שער אינה נכנסת לווידאו
    assert "thumbnail" not in TIMELINE_ROLES | COMPOSITE_ROLES


def test_placement_inserted_into_clip():
    clip_id = _make_clip(duration=20.0)
    image_id = _make_image()

    pid = image_assets.add_placement(
        clip_id=clip_id, image_id=image_id, role="insert",
        at_time=8.0, duration=3.0)

    with session_scope() as s:
        row = s.get(ImagePlacement, pid)
        assert row is not None
        assert row.role is ImageRole.INSERT
        assert abs(row.at_time - 8.0) < 0.01
        assert abs(row.duration - 3.0) < 0.01
        items = image_assets.placements_for_clip(s, clip_id)
        assert len(items) == 1
        timeline, composite = image_assets.split_placements(items)
        assert len(timeline) == 1 and not composite


def test_placement_rejects_unknown_role_and_missing_image():
    clip_id = _make_clip()
    image_id = _make_image()
    try:
        image_assets.add_placement(clip_id=clip_id, image_id=image_id,
                                   role="sparkles", at_time=1, duration=2)
        raise AssertionError("תפקיד פסול התקבל")
    except image_assets.InvalidRoleError:
        pass

    try:
        image_assets.add_placement(clip_id=clip_id, image_id="nope",
                                   role="insert", at_time=1, duration=2)
        raise AssertionError("תמונה לא קיימת התקבלה")
    except image_assets.ImageNotFoundError:
        pass

    broken = _make_image(ready=False)
    try:
        image_assets.add_placement(clip_id=clip_id, image_id=broken,
                                   role="insert", at_time=1, duration=2)
        raise AssertionError("תמונה שאינה מוכנה התקבלה")
    except image_assets.ImageNotReadyError:
        pass


def test_placement_beyond_clip_end_is_rejected():
    clip_id = _make_clip(duration=10.0)
    image_id = _make_image()
    try:
        image_assets.add_placement(clip_id=clip_id, image_id=image_id,
                                   role="broll", at_time=9.95, duration=3.0)
        raise AssertionError("שיבוץ מעבר לסוף הקליפ התקבל")
    except image_assets.InvalidPlacementError as exc:
        assert exc.hint


# ==========================================================================
# 6. מיפוי משך התמונה
# ==========================================================================
def test_image_duration_is_clamped():
    # קצר מדי ננעל למינימום, ארוך מדי לתקרה
    role, at, dur = image_assets.validate_placement(
        role="insert", at_time=5.0, duration=0.01, clip_duration=30.0)
    assert dur == image_assets.MIN_PLACEMENT_SECONDS
    role, at, dur = image_assets.validate_placement(
        role="insert", at_time=5.0, duration=900.0, clip_duration=300.0)
    assert dur == image_assets.MAX_PLACEMENT_SECONDS
    # 0 => ברירת מחדל סבירה
    role, at, dur = image_assets.validate_placement(
        role="insert", at_time=5.0, duration=0.0, clip_duration=30.0)
    assert dur == 3.0

    # פתיח תמיד ב-0, סיום תמיד בסוף
    _, at_in, _ = image_assets.validate_placement(
        role="intro", at_time=9.0, duration=2.0, clip_duration=30.0)
    assert at_in == 0.0
    _, at_out, _ = image_assets.validate_placement(
        role="outro", at_time=1.0, duration=2.0, clip_duration=30.0)
    assert at_out == 30.0
    # רקע פרוס על כל הקליפ
    _, _, dur_bg = image_assets.validate_placement(
        role="background", at_time=4.0, duration=2.0, clip_duration=30.0)
    assert dur_bg == 30.0
    # תמונת שער אינה תופסת זמן
    _, _, dur_th = image_assets.validate_placement(
        role="thumbnail", at_time=4.0, duration=5.0, clip_duration=30.0)
    assert dur_th == 0.0


def test_timeline_plan_accumulates_shift_in_order():
    items = [
        {"role": "insert", "at_time": 12.0, "duration": 3.0, "path": "c"},
        {"role": "intro", "at_time": 0.0, "duration": 2.0, "path": "a"},
        {"role": "insert", "at_time": 5.0, "duration": 1.5, "path": "b"},
        {"role": "outro", "at_time": 0.0, "duration": 4.0, "path": "d"},
    ]
    plan = image_render.build_timeline_plan(items, clip_duration=20.0)
    assert [p["path"] for p in plan] == ["a", "b", "c", "d"]
    assert [p["at"] for p in plan] == [0.0, 5.0, 12.0, 20.0]
    # ההזזה המצטברת לפני כל פריט
    assert [round(p["shift_before"], 2) for p in plan] == [0.0, 2.0, 3.5, 6.5]
    total = sum(p["seconds"] for p in plan)
    assert abs(total - 10.5) < 0.01


def test_cue_shift_matches_inserted_images():
    class FakeCue:
        def __init__(self, start, end, words=None):
            self.start, self.end, self.words = start, end, words or []

    cues = [FakeCue(1.0, 2.0), FakeCue(6.0, 7.0,
                                       [{"start": 6.0, "end": 6.5}]),
            FakeCue(14.0, 15.0)]
    plan = [{"at": 0.0, "seconds": 2.0}, {"at": 10.0, "seconds": 3.0}]
    moved = image_render.shift_cues_for_inserts(cues, plan)

    assert moved == 3
    assert cues[0].start == 3.0            # אחרי הפתיח בלבד
    assert cues[1].start == 8.0            # אחרי הפתיח בלבד
    assert cues[2].start == 19.0           # אחרי הפתיח וההכנסה
    assert cues[1].words[0]["start"] == 8.0


def test_composite_graph_does_not_change_duration():
    items = [
        {"role": "broll", "at_time": 3.0, "duration": 2.0, "path": "x.png",
         "opacity": 1.0, "scale": 1.0, "position": "center", "fit": "cover"},
        {"role": "overlay", "at_time": 8.0, "duration": 2.0, "path": "y.png",
         "opacity": 0.8, "scale": 0.3, "position": "top_right", "fit": "cover"},
    ]
    graph, label, extra = image_render.build_composite_graph(
        items, width=720, height=1280)
    assert extra == 2
    assert "enable='between(t,3.000,5.000)'" in graph
    assert "enable='between(t,8.000,10.000)'" in graph
    assert "colorchannelmixer=aa=0.800" in graph
    assert label.startswith("[cv")
    # סדר הקלטים תואם לגרף
    args = image_render.composite_inputs(items)
    assert args.count("-i") == 2


# ==========================================================================
# 7. בידוד: כשל בתמונות אינו מפיל את העורך
# ==========================================================================
def test_missing_image_file_is_skipped_not_fatal():
    clip_id = _make_clip()
    image_id = _make_image()
    image_assets.add_placement(clip_id=clip_id, image_id=image_id,
                               role="insert", at_time=5.0, duration=2.0)
    # מוחקים את הקובץ מתחת לרגליים
    with session_scope() as s:
        row = s.get(GeneratedImage, image_id)
        Path(row.file_path).unlink(missing_ok=True)
        items = image_assets.placements_for_clip(s, clip_id)
    # שיבוץ עם קובץ חסר מדולג בשקט, ולא מפיל את הרינדור
    assert items == []


# ==========================================================================
# 8. אימות כתובת לייב
# ==========================================================================
def test_live_url_validation_detects_platforms():
    cases = [
        ("https://www.twitch.tv/somechannel", SourceKind.TWITCH_LIVE, True),
        ("https://twitch.tv/videos/12345", SourceKind.TWITCH_VOD, False),
        ("https://www.youtube.com/live/abc123", SourceKind.YOUTUBE_LIVE, True),
        ("https://www.youtube.com/watch?v=abc123", SourceKind.YOUTUBE_VOD, False),
        ("https://kick.com/somechannel", SourceKind.KICK_LIVE, True),
    ]
    for url, kind, is_live in cases:
        r = ingest.resolve_url(url)
        assert r.kind == kind, f"{url} -> {r.kind}"
        assert r.is_live == is_live, f"{url} -> is_live={r.is_live}"


def test_unsupported_url_is_rejected():
    for bad in ("", "   ", "not a url", "ftp://example.com/x",
                "javascript:alert(1)"):
        try:
            ingest.resolve_url(bad)
            raise AssertionError(f"כתובת פסולה התקבלה: {bad!r}")
        except PolixorError as exc:
            assert exc.code in ("invalid_url", "unsupported_platform")


# ==========================================================================
# 9. מעברי מצב בקליטת לייב
# ==========================================================================
def test_live_state_transitions_are_enforced():
    # מסלול תקין
    assert live_can_transition("idle", "detecting")
    assert live_can_transition("detecting", "connecting")
    assert live_can_transition("connecting", "live")
    assert live_can_transition("live", "reconnecting")
    assert live_can_transition("reconnecting", "live")
    assert live_can_transition("live", "stopping")
    assert live_can_transition("stopping", "completed")

    # קפיצות אסורות
    assert not live_can_transition("idle", "live")
    assert not live_can_transition("completed", "live")
    assert not live_can_transition("failed", "live")
    assert not live_can_transition("stopping", "live")


def test_state_machine_rejects_illegal_transition():
    seen: list[str] = []
    sm = StateMachine(lambda s, d: seen.append(s))
    assert sm.to(LiveState.DETECTING.value)
    assert not sm.to(LiveState.COMPLETED.value)   # detecting -> completed אסור
    assert sm.state == "detecting"
    assert seen == ["detecting"]
    assert sm.to(LiveState.CONNECTING.value)
    assert sm.to(LiveState.LIVE.value)
    assert sm.is_terminal is False
    assert sm.to(LiveState.STOPPING.value)
    assert sm.to(LiveState.COMPLETED.value)
    assert sm.is_terminal is True


# ==========================================================================
# 10. מצב חיבור מחדש
# ==========================================================================
def _fake_capture(script, *, stop_after=None, backoff=(0.0,) * 6,
                  max_failures=6):
    """מריץ לולאת קליטה עם מקטעים מתוכנתים מראש."""
    work = Path(tempfile.mkdtemp(prefix="cap_"))
    states: list[str] = []
    sm = StateMachine(lambda s, d: states.append(s))
    calls = {"n": 0}
    stop = threading.Event()

    def record(manifest, dest, *, seconds, cancel_event, stop_event,
               on_tick=None):
        idx = calls["n"]
        calls["n"] += 1
        spec = script[idx] if idx < len(script) else script[-1]
        if spec is None:
            raise live.LiveUnavailableError("הזרם נפל")
        secs, complete = spec
        dest.write_bytes(b"x" * 4096)
        if on_tick:
            on_tick(0.5, 4096)
        if stop_after is not None and calls["n"] >= stop_after:
            stop_event.set()
        return SegmentResult(path=dest, seconds=secs, complete=complete,
                             returncode=0 if complete else 1,
                             error="" if complete else "הזרם נפל")

    outcome = run_capture(
        CaptureConfig(url="https://x/live", work_dir=work,
                      segment_seconds=10.0, backoff=backoff,
                      max_failures=max_failures),
        settings=AppSettings(), cancel_event=threading.Event(),
        stop_event=stop, machine=sm,
        resolve=lambda u, s: "manifest://ok", record=record,
        sleep=lambda *_: None)
    return outcome, states


def test_reconnect_state_is_entered_on_drop():
    # מקטע מלא, נפילה, ואז שני מקטעים מלאים ועצירה
    outcome, states = _fake_capture(
        [(10.0, True), (4.0, False), (10.0, True), (10.0, True)],
        stop_after=4)

    assert "reconnecting" in states, states
    assert outcome.reconnects >= 1
    assert outcome.state == "completed"
    assert states[0] == "connecting"
    assert states[-1] == "completed"


def test_reconnect_gives_up_after_max_failures():
    outcome, states = _fake_capture([(10.0, True)] + [(0.2, False)] * 20,
                                    max_failures=3)
    # נאסף חומר, ולכן הסיום הוא completed ולא failed
    assert outcome.state == "completed", states
    assert outcome.seconds >= 10.0
    assert outcome.error


def test_capture_fails_cleanly_when_nothing_collected():
    outcome, states = _fake_capture([None] * 20, max_failures=2)
    assert outcome.state == "failed", states
    assert not outcome.segments
    assert outcome.error


# ==========================================================================
# 11. עצירת הקלטה
# ==========================================================================
def test_stop_capture_finishes_current_segment():
    outcome, states = _fake_capture([(10.0, True)] * 5, stop_after=2)
    assert outcome.stopped_by_user is True
    assert outcome.state == "completed"
    assert len(outcome.segments) == 2
    assert "stopping" in states


# ==========================================================================
# 12. שמירת חומר חלקי
# ==========================================================================
def test_partial_capture_is_preserved_through_drop():
    # 12 שניות, נפילה אחרי 4 שניות, ואז 10 נוספות
    outcome, _ = _fake_capture(
        [(12.0, True), (4.0, False), (10.0, True)], stop_after=3)

    total = sum(s["seconds"] for s in outcome.segments)
    assert abs(total - 26.0) < 0.01, total
    # המקטע שנקטע נשמר ולא נזרק
    assert any(abs(s["seconds"] - 4.0) < 0.01 for s in outcome.segments)
    assert all(Path(s["path"]).exists() for s in outcome.segments)
    assert abs(outcome.seconds - 26.0) < 0.01


def test_too_short_segment_is_discarded():
    # מקטע קצר מהמינימום אינו נחשב חומר
    outcome, _ = _fake_capture([(0.2, False), (10.0, True)], stop_after=2)
    assert all(s["seconds"] >= live.MIN_USABLE_SEGMENT
               for s in outcome.segments)


# ==========================================================================
# 13. לייב → הפייפליין הרגיל
# ==========================================================================
def test_live_capture_becomes_a_normal_source():
    from polixor.pipeline import _plan_stages, _source_kind_for

    st = AppSettings()
    live_stages = [s.value for s in _plan_stages(
        st, has_local_source=False, is_live=True)]
    vod_stages = [s.value for s in _plan_stages(
        st, has_local_source=False, is_live=False)]

    # ההבדל היחיד הוא שלב הקליטה במקום ההורדה
    assert live_stages[0] == "capture"
    assert vod_stages[0] == "download"
    assert live_stages[1:] == vod_stages[1:], (live_stages, vod_stages)
    # כלומר: תמלול, ניתוח, בחירה וייצוא זהים לחלוטין
    for stage in ("transcribe", "analyze", "select"):
        assert stage in live_stages

    assert _source_kind_for("twitch_live") is SourceKind.TWITCH_LIVE
    assert _source_kind_for("nonsense") is SourceKind.UNKNOWN


def test_live_segments_concat_into_one_source():
    """מקטעים פגומים מדולגים, ולא מפילים את איחוד ההקלטה."""
    work = Path(tempfile.mkdtemp(prefix="concat_"))
    empty = work / "empty.ts"
    empty.write_bytes(b"")
    tiny = work / "tiny.ts"
    tiny.write_bytes(b"\x00" * 64)
    try:
        live.concat_segments([empty, tiny], work / "out.mp4")
        raise AssertionError("איחוד ללא חומר שמיש היה אמור להיכשל")
    except PolixorError as exc:
        assert exc.code in ("live_not_started", "ffmpeg_failed")
        assert exc.message


def test_total_recorded_seconds():
    segs = [{"seconds": 10.0}, {"seconds": 4.25}, {"seconds": 0}]
    assert live.total_recorded_seconds(segs) == 14.25


# ==========================================================================
# Suggest Visuals – הצעות בלבד, ללא הוספה אוטומטית
# ==========================================================================
def test_suggest_visuals_finds_descriptive_lines():
    from polixor.models import SubtitleCue

    clip_id = _make_clip(duration=30.0)
    lines = [
        (0.0, 3.0, "שלום לכולם וברוכים הבאים"),
        (3.2, 7.0, "ואז הגעתי למקום הכי נמוך בחיים שלי"),
        (7.2, 9.0, "כן"),
        (12.0, 16.0, "נסעתי לים בשלוש לפנות בוקר"),
        (22.0, 26.0, "והיום ניצחתי את כולם בטורניר"),
    ]
    with session_scope() as s:
        for i, (a, b, t) in enumerate(lines):
            s.add(SubtitleCue(clip_id=clip_id, idx=i, start=a, end=b, text=t))

    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        out = visual_suggest.suggest_for_clip(s, clip, limit=5,
                                              settings=AppSettings())

    assert out["source"] == "heuristic"
    items = out["suggestions"]
    assert items, "לא נמצאו הצעות למשפטים תיאוריים"
    # המשפט הרגשי חייב להופיע
    assert any("נמוך" in i["text"] for i in items)
    # פרומפט באנגלית גם למשפט עברי
    for i in items:
        assert i["prompt"]
        assert not any("֐" <= ch <= "׿" for ch in i["prompt"]), i["prompt"]
        assert "no text" in i["prompt"]
    # הצעות בלבד: אין שיבוץ ואין תמונה שנוצרה
    with session_scope() as s:
        assert s.query(ImagePlacement).filter(
            ImagePlacement.clip_id == clip_id).count() == 0


def test_suggest_visuals_without_transcript_says_so():
    clip_id = _make_clip(duration=12.0)
    with session_scope() as s:
        out = visual_suggest.suggest_for_clip(
            s, s.get(Clip, clip_id), settings=AppSettings())
    assert out["suggestions"] == []
    assert out["source"] == "none"
    assert "תמלול" in out["note"]


def test_suggestions_are_spaced_apart():
    from polixor.models import SubtitleCue

    clip_id = _make_clip(duration=60.0)
    with session_scope() as s:
        for i in range(10):
            s.add(SubtitleCue(clip_id=clip_id, idx=i, start=i * 2.0,
                              end=i * 2.0 + 1.8,
                              text="ואז הגעתי למקום הכי נמוך בחיים שלי"))
    with session_scope() as s:
        out = visual_suggest.suggest_for_clip(
            s, s.get(Clip, clip_id), limit=6, settings=AppSettings())
    times = [i["end"] for i in out["suggestions"]]
    for a, b in zip(times, times[1:]):
        assert b - a >= visual_suggest.MIN_GAP_SECONDS - 0.01, times


# ==========================================================================
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  \033[91m✗\033[0m {name}: {exc}")
            failed.append(name)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} בדיקות עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
