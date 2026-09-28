"""
בדיקות ל-QA שאחרי הרינדור.

כל בדיקה כאן מייצרת קובץ עם **תקלה אמיתית** — שחור באמצע, תמונה
קפואה, פס קול חסר, אורך שגוי — ומוודאת שה-QA מוצא אותה. בדיקה
שרק מריצה את הקוד על קובץ תקין לא מוכיחה שהיא תתפוס משהו.

הרצה:  python3 tests/test_render_qa.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxqa_"))

from polixor.config import PATHS                                 # noqa: E402

PATHS.ensure()

from polixor.services import render_qa as qa                     # noqa: E402
from polixor.services.subtitles import Cue                       # noqa: E402
from polixor.util.ffmpeg import ffmpeg_bin                       # noqa: E402

TMP = Path(os.environ["POLIXOR_DATA_DIR"]) / "qa"
TMP.mkdir(parents=True, exist_ok=True)
_BUILT: dict[str, Path] = {}


def ff(args: list[str], dst: Path) -> Path:
    res = subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel",
                          "error"] + args + [str(dst)],
                         capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg נכשל: {res.stderr[-400:]}")
    return dst


def good(seconds: float = 8.0) -> Path:
    """קליפ תקין: תמונה שמשתנה כל הזמן ופס קול."""
    key = f"good{seconds}"
    if key not in _BUILT:
        _BUILT[key] = ff([
            "-f", "lavfi", "-i", f"testsrc2=s=320x180:d={seconds}:r=25",
            "-f", "lavfi", "-i", f"sine=f=300:d={seconds}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-shortest"], TMP / f"good_{seconds}.mp4")
    return _BUILT[key]


def black_middle() -> Path:
    """תקין בקצוות, שחור לגמרי בין 3 ל-6 שניות."""
    if "black" not in _BUILT:
        _BUILT["black"] = ff([
            "-f", "lavfi", "-i", "testsrc2=s=320x180:d=9:r=25",
            "-f", "lavfi", "-i", "sine=f=300:d=9",
            "-vf", "geq=lum='if(between(T,3,6),0,lum(X,Y))':"
                   "cb='if(between(T,3,6),128,cb(X,Y))':"
                   "cr='if(between(T,3,6),128,cr(X,Y))'",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-shortest"], TMP / "black.mp4")
    return _BUILT["black"]


def frozen() -> Path:
    """תמונה סטטית לגמרי לאורך כל הקליפ."""
    if "frozen" not in _BUILT:
        _BUILT["frozen"] = ff([
            "-f", "lavfi", "-i", "color=c=teal:s=320x180:d=9:r=25",
            "-f", "lavfi", "-i", "sine=f=300:d=9",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-shortest"], TMP / "frozen.mp4")
    return _BUILT["frozen"]


def silent_video() -> Path:
    """וידאו בלי פס קול בכלל."""
    if "silent" not in _BUILT:
        _BUILT["silent"] = ff([
            "-f", "lavfi", "-i", "testsrc2=s=320x180:d=6:r=25",
            "-pix_fmt", "yuv420p", "-c:v", "libx264",
            "-preset", "veryfast"], TMP / "silent.mp4")
    return _BUILT["silent"]


def all_black() -> Path:
    if "allblack" not in _BUILT:
        _BUILT["allblack"] = ff([
            "-f", "lavfi", "-i", "color=c=black:s=320x180:d=6:r=25",
            "-f", "lavfi", "-i", "sine=f=300:d=6",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-shortest"], TMP / "allblack.mp4")
    return _BUILT["allblack"]


# ==========================================================================
# קובץ תקין
# ==========================================================================
def test_a_good_clip_passes_cleanly():
    r = qa.check_render(good(), expected_duration=8.0, expect_audio=True)
    assert r.passed, [f.to_dict() for f in r.findings]
    assert not r.needs_review
    assert "streams" in r.checks_run and "duration" in r.checks_run
    assert "frame_samples" in r.checks_run


def test_report_is_json_serialisable():
    r = qa.check_render(good(), expected_duration=8.0)
    blob = json.dumps(r.to_dict(), ensure_ascii=False)
    assert "findings" in blob and "checks_run" in blob


def test_every_check_is_either_run_or_explicitly_skipped():
    """בדיקה שלא רצה חייבת להיאמר, ולא לעבור בשקט."""
    r = qa.check_render(good(), expected_duration=0.0, cues=None)
    assert "duration" in r.checks_skipped, r.to_dict()
    assert "caption_bounds" in r.checks_skipped
    for reason in r.checks_skipped.values():
        assert reason, r.checks_skipped


# ==========================================================================
# תקלות אמיתיות
# ==========================================================================
def test_black_section_in_the_middle_is_caught():
    r = qa.check_render(black_middle(), expected_duration=9.0)
    codes = {f.code for f in r.findings}
    assert "black_frames" in codes, r.to_dict()
    assert r.needs_review
    finding = [f for f in r.findings if f.code == "black_frames"][0]
    assert finding.severity == "error"
    spans = finding.detail["spans"]
    assert any(2.5 <= a <= 3.6 for a, _ in spans), spans


def test_a_fully_frozen_clip_is_caught():
    r = qa.check_render(frozen(), expected_duration=9.0)
    codes = {f.code for f in r.findings}
    assert "frozen_sections" in codes, r.to_dict()
    finding = [f for f in r.findings if f.code == "frozen_sections"][0]
    assert finding.severity == "error", finding.to_dict()
    assert r.needs_review


def test_a_moving_clip_is_not_reported_as_frozen():
    r = qa.check_render(good(), expected_duration=8.0)
    assert "frozen_sections" not in {f.code for f in r.findings}
    assert r.measurements.get("frozen_seconds", 0.0) < 1.0


def test_missing_audio_is_caught_when_expected():
    r = qa.check_render(silent_video(), expected_duration=6.0,
                        expect_audio=True)
    assert "no_audio" in {f.code for f in r.findings}
    assert r.needs_review


def test_missing_audio_is_fine_when_not_expected():
    r = qa.check_render(silent_video(), expected_duration=6.0,
                        expect_audio=False)
    assert "no_audio" not in {f.code for f in r.findings}


def test_all_black_clip_is_caught_by_frame_sampling():
    r = qa.check_render(all_black(), expected_duration=6.0)
    codes = {f.code for f in r.findings}
    assert "all_frames_black" in codes, r.to_dict()
    assert r.needs_review


def test_duration_mismatch_is_caught():
    r = qa.check_render(good(), expected_duration=20.0)
    f = [x for x in r.findings if x.code == "duration_mismatch"]
    assert f, r.to_dict()
    assert f[0].severity == "error"
    assert r.measurements["duration_delta"] < -10


def test_duration_within_tolerance_passes():
    r = qa.check_render(good(), expected_duration=8.0 + 0.2)
    assert "duration_mismatch" not in {f.code for f in r.findings}


def test_missing_file_is_an_error_not_a_crash():
    r = qa.check_render(TMP / "nope.mp4", expected_duration=5.0)
    assert "missing_output" in {f.code for f in r.findings}
    assert r.needs_review


def test_unreadable_file_is_an_error():
    bad = TMP / "garbage.mp4"
    bad.write_bytes(b"\x00" * 4096)
    r = qa.check_render(bad, expected_duration=5.0)
    assert r.needs_review
    assert {f.code for f in r.findings} & {"unreadable", "no_video"}


# ==========================================================================
# כתוביות ואזור בטוח
# ==========================================================================
def test_caption_past_the_end_is_caught():
    cues = [Cue(0.0, 2.0, "ראשונה"), Cue(6.0, 11.5, "חורגת")]
    r = qa.check_render(good(), expected_duration=8.0, cues=cues)
    f = [x for x in r.findings if x.code == "caption_out_of_bounds"]
    assert f, r.to_dict()
    assert f[0].severity == "error"
    assert f[0].detail["count"] == 1


def test_captions_inside_the_clip_pass():
    cues = [Cue(0.0, 2.0, "ראשונה"), Cue(2.2, 5.0, "שנייה")]
    r = qa.check_render(good(), expected_duration=8.0, cues=cues)
    assert "caption_out_of_bounds" not in {f.code for f in r.findings}
    assert r.measurements["cue_count"] == 2


def test_negative_caption_start_is_caught():
    cues = [Cue(-0.5, 2.0, "לפני ההתחלה")]
    r = qa.check_render(good(), expected_duration=8.0, cues=cues)
    assert "caption_negative_start" in {f.code for f in r.findings}


def test_caption_below_the_safe_area_is_flagged():
    """שוליים קטנים מהאזור השמור — הכתובית תיחתך בפיד."""
    r = qa.check_render(good(), expected_duration=8.0,
                        vertical=True, safe_margin_v=5)
    f = [x for x in r.findings if x.code == "caption_outside_safe_area"]
    assert f, r.to_dict()
    assert f[0].severity == "warning"
    assert f[0].detail["required"] > 5


def test_caption_inside_the_safe_area_passes():
    from polixor.services.caption_engine import SafeZone, safe_margins

    zone = SafeZone.for_frame(vertical=True)
    margin_v, _ = safe_margins(320, 180, zone, "bottom")
    r = qa.check_render(good(), expected_duration=8.0,
                        vertical=True, safe_margin_v=margin_v)
    assert "caption_outside_safe_area" not in {f.code for f in r.findings}


# ==========================================================================
# סיווג חומרה
# ==========================================================================
def test_warnings_alone_do_not_block_completion():
    """אזהרה אינה סיבה לסמן needs_review — רק בעיה אמיתית."""
    r = qa.QAReport()
    r.add("something", "warning", "הערה")
    assert not r.needs_review and not r.passed
    r.add("other", "error", "בעיה")
    assert r.needs_review


def test_summary_describes_what_ran():
    r = qa.check_render(good(), expected_duration=8.0)
    assert "בדיקות" in r.summary()
    assert len(r.checks_run) >= 4


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
    print(f"\n{passed}/{len(fns)} בדיקות QA עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
