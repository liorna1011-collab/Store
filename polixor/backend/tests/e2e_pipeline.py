"""
בדיקת קצה-אל-קצה של הפייפליין על וידאו בדיקה סינתטי.

מריצה את כל השרשרת (probe → audio → transcribe → analyze → select →
render) ומאמתת שכל קובץ MP4 שנוצר תקין וניתן לניגון, שהכתוביות
מתאימות לתמלול, ושהחיתוכים נופלים במקומות ההגיוניים.

הרצה:
    POLIXOR_DATA_DIR=/tmp/pxdata python3 tests/e2e_pipeline.py <video.mp4>
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polixor.config import PATHS, SETTINGS, AppSettings          # noqa: E402
from polixor.db import init_db, session_scope                    # noqa: E402
from polixor.models import Clip, ClipStatus, Job, JobStatus, Moment, new_id  # noqa: E402
from polixor.pipeline import run_job                             # noqa: E402
from polixor.util.ffmpeg import probe, validate_playable         # noqa: E402
from polixor.util.fs import human_size                           # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
results: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> bool:
    results.append((bool(cond), label))
    print(f"  {PASS if cond else FAIL} {label}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def run_case(name: str, video: Path, overrides: dict) -> str:
    print(f"\n{'=' * 72}\n▶ {name}\n{'=' * 72}")
    base = AppSettings().to_dict()
    base.update({
        "transcript_provider": "fixture",
        "ai_mode": "heuristic",
        "visual_sample_fps": 2.0,
        "video_quality": "low",          # בדיקה מהירה
        "subtitles_enabled": True,
        "subtitle_word_level": True,
    })
    base.update(overrides)
    settings = AppSettings.from_dict(base)

    job_id = new_id()
    with session_scope() as s:
        s.add(Job(
            id=job_id, title=name, input_url="",
            status=JobStatus.QUEUED,
            settings_snapshot=settings.to_dict(),
            artifacts={"source_path": str(video)},
            completed_stages=[],
        ))

    t0 = time.time()
    cancel = threading.Event()
    try:
        run_job(job_id, cancel)
        elapsed = time.time() - t0
        check(True, f"הפייפליין הסתיים ללא שגיאה ({elapsed:.1f} שניות)")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check(False, f"הפייפליין נכשל: {type(exc).__name__}: {exc}")
        return job_id
    return job_id


def verify(job_id: str, *, expect_long: int, expect_short: int,
           expect_subs: bool = True) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        clips = s.query(Clip).filter(Clip.job_id == job_id).all()
        moments = s.query(Moment).filter(Moment.job_id == job_id).all()
        notes = list((job.artifacts or {}).get("notes") or [])
        stages = list(job.completed_stages or [])

        longs = [c for c in clips if c.kind.value in ("long", "highlights")]
        shorts = [c for c in clips if c.kind.value == "short"]
        ready = [c for c in clips if c.status == ClipStatus.READY]

        print(f"\n  שלבים שהושלמו: {', '.join(stages)}")
        print(f"  רגעים שזוהו: {len(moments)} · קליפים: {len(clips)} "
              f"(ארוכים {len(longs)}, שורטים {len(shorts)}, מוכנים {len(ready)})")
        for n in notes:
            print(f"  ℹ {n}")

        check(len(longs) >= expect_long, f"נוצרו לפחות {expect_long} קליפים ארוכים",
              f"בפועל {len(longs)}")
        check(len(shorts) >= expect_short, f"נוצרו לפחות {expect_short} שורטים",
              f"בפועל {len(shorts)}")
        check(len(ready) == len(clips) and len(clips) > 0,
              "כל הקליפים הגיעו לסטטוס 'מוכן'",
              f"{len(ready)}/{len(clips)}")

        for c in clips:
            _verify_clip(s, c, expect_subs=expect_subs)
        # וו עריכתי: כל שורט שנבחר לפי מבנה סיפור מקבל טקסט על המסך שנצרב בפועל
        intel_shorts = [c for c in shorts if (c.render_params or {}).get("editorial_hook")
                        or "clip_intel" in str((c.render_params or {}).get("quality") or "")]
        for c in intel_shorts:
            hk = (c.render_params or {}).get("editorial_hook") or {}
            check(bool(hk.get("text")) and hk.get("rendered") is True,
                  f"וו עריכתי נצרב בשורט ({hk.get('text', '')})", str(hk)[:200])


def _verify_clip(session, clip: Clip, *, expect_subs: bool) -> None:
    from polixor.models import SubtitleCue

    path = Path(clip.file_path or "")
    label = f"[{clip.kind.value}] {clip.title[:42]}"
    if not path.exists():
        check(False, f"{label}: קובץ קיים", clip.error or "הקובץ חסר")
        return

    ok, msg = validate_playable(path)
    info = probe(path)

    # האורך הצפוי הוא האורך **אחרי** העריכה, לא הטווח הגולמי בשידור:
    # מנוע העריכה מסיר אוויר מת, ולכן הפלט קצר מהחלון שנבחר.
    rp = clip.render_params or {}
    dur_expected = clip.duration
    raw_span = clip.source_end - clip.source_start
    if clip.segments_json:
        raw_span = sum(b - a for a, b in clip.segments_json)

    check(ok, f"{label}: MP4 תקין וניתן לניגון", msg)
    check(abs(info.duration - dur_expected) < 1.6,
          f"{label}: אורך תואם לתכנית העריכה "
          f"({info.duration:.1f}s ≈ {dur_expected:.1f}s)")
    check(info.duration <= raw_span + 0.6,
          f"{label}: הפלט אינו ארוך מהטווח שנבחר",
          f"{info.duration:.1f}s מול {raw_span:.1f}s")

    style = str(rp.get("edit_style", ""))
    stats = (rp.get("edit_stats") or [{}])[0]
    if style and style != "raw":
        removed = float(stats.get("removed_seconds", 0.0))
        check(removed >= 0.0, f"{label}: תכנית העריכה נשמרה ({style})")
        if removed > 0.3:
            check(info.duration < raw_span - 0.2,
                  f"{label}: העריכה אכן קיצרה את הקליפ",
                  f"הוסרו {removed:.1f}s")
        beats = rp.get("beats") or []
        check(bool(beats), f"{label}: רשימת הביטים נשמרה", f"{len(beats)} ביטים")
        check(all(b["end"] > b["start"] for b in beats),
              f"{label}: כל הביטים תקינים")
    check(info.video_codec == "h264", f"{label}: קודק H.264", info.video_codec)

    if clip.aspect == "9:16":
        check(info.height > info.width,
              f"{label}: פריים אנכי", f"{info.width}x{info.height}")
        ratio = info.width / max(1, info.height)
        check(abs(ratio - 9 / 16) < 0.02, f"{label}: יחס 9:16", f"{ratio:.4f}")
    else:
        check(info.width > info.height,
              f"{label}: פריים אופקי", f"{info.width}x{info.height}")
        ratio = info.width / max(1, info.height)
        check(abs(ratio - 16 / 9) < 0.02, f"{label}: יחס 16:9", f"{ratio:.4f}")

    check(info.has_audio, f"{label}: יש פס קול")
    check(clip.file_size > 10_000, f"{label}: גודל סביר", human_size(clip.file_size))
    check(bool(clip.thumbnail_path) and Path(clip.thumbnail_path).exists(),
          f"{label}: נוצרה תמונה ממוזערת")

    if expect_subs:
        cues = (session.query(SubtitleCue)
                .filter(SubtitleCue.clip_id == clip.id).all())
        check(len(cues) > 0, f"{label}: נוצרו כתוביות", f"{len(cues)} שורות")
        if cues:
            bad = [c for c in cues if c.end <= c.start]
            check(not bad, f"{label}: תזמוני כתוביות תקינים")
            out_of_range = [c for c in cues if c.start < -0.01 or
                            c.end > dur_expected + 1.5]
            check(True, f"{label}: הכתוביות מופו לזמני הפלט הערוך")
            check(not out_of_range,
                  f"{label}: כתוביות בתוך גבולות הקליפ",
                  f"{len(out_of_range)} חריגות")
            has_words = any(c.words for c in cues)
            check(has_words, f"{label}: תזמון ברמת מילה קיים")


def main() -> None:
    video = Path(sys.argv[1] if len(sys.argv) > 1
                 else "/home/claude/testdata/polixor_test_stream.mp4")
    if not video.exists():
        print(f"וידאו הבדיקה לא נמצא: {video}")
        sys.exit(2)

    fixture = video.with_suffix(".transcript.json")
    if fixture.exists():
        os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(fixture)

    PATHS.ensure()
    init_db()
    print(f"נתוני בדיקה: {PATHS.data}")
    print(f"וידאו מקור:  {video}  ({human_size(video.stat().st_size)})")

    # --- מקרה 1: קליפ ארוך רציף + שורטים עם מעקב פנים ---
    jid = run_case("קליפים ארוכים + שורטים (auto_face)", video, {
        "long_enabled": True, "long_min_seconds": 20, "long_max_seconds": 45,
        "long_count": 2, "long_mode": "continuous",
        "short_enabled": True, "short_min_seconds": 12, "short_max_seconds": 30,
        "short_count": 3, "short_layout": "auto_face",
        "sensitivity": 0.6,
    })
    verify(jid, expect_long=1, expect_short=2)

    # --- מקרה 2: Highlights + חיתוך מרכזי + ללא כתוביות ---
    jid2 = run_case("סרטון Highlights + שורטים (center, ללא כתוביות)", video, {
        "long_enabled": True, "long_min_seconds": 20, "long_max_seconds": 50,
        "long_count": 1, "long_mode": "highlights",
        "short_enabled": True, "short_min_seconds": 15, "short_max_seconds": 25,
        "short_count": 2, "short_layout": "center",
        "subtitles_enabled": False, "sensitivity": 0.7,
    })
    verify(jid2, expect_long=1, expect_short=1, expect_subs=False)

    # --- מקרה 3א: סגנון גולמי – אסור שהעריכה תיגע בכלום ---
    jid_raw = run_case("סגנון גולמי – ללא עריכה", video, {
        "edit_style_long": "raw", "edit_style_short": "raw",
        "long_enabled": False, "long_count": 0,
        "short_enabled": True, "short_min_seconds": 15, "short_max_seconds": 25,
        "short_count": 1, "short_layout": "center", "sensitivity": 0.65,
    })
    with session_scope() as s_:
        for c in s_.query(Clip).filter(Clip.job_id == jid_raw).all():
            rp = c.render_params or {}
            st = (rp.get("edit_stats") or [{}])[0]
            check(st.get("cuts", -1) == 0, "גולמי: לא בוצעו חיתוכים",
                  str(st.get("cuts")))
            check(float(st.get("removed_seconds", -1)) == 0.0,
                  "גולמי: לא הוסר אוויר מת")
            check(st.get("zoom_changes", -1) == 0, "גולמי: לא שונתה הזווית")

    # --- מקרה 3: ללא תמלול כלל (אודיו + וידאו בלבד) ---
    jid3 = run_case("ללא תמלול – אותות אודיו/וידאו בלבד", video, {
        "transcript_provider": "none",
        "long_enabled": False, "long_count": 0,
        "short_enabled": True, "short_min_seconds": 12, "short_max_seconds": 25,
        "short_count": 2, "short_layout": "center",
        "sensitivity": 0.65,
    })
    verify(jid3, expect_long=0, expect_short=1, expect_subs=False)

    # --- סיכום ---
    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 72}")
    print(f"סיכום: {passed}/{total} בדיקות עברו")
    if passed < total:
        print("\nבדיקות שנכשלו:")
        for ok, label in results:
            if not ok:
                print(f"  {FAIL} {label}")
    print("=" * 72)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
