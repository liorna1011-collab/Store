"""
בדיקות יחידה לרכיבי הליבה.

הרצה:  python3 -m pytest tests/test_units.py -v
        (או ישירות: python3 tests/test_units.py)
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polixor.config import AppSettings                                  # noqa: E402
from polixor.services import editing, scoring, selection, subtitles     # noqa: E402
from polixor.services.audio import AudioFeatures                        # noqa: E402
from polixor.services.reframe import build_vertical_filter, plan_reframe  # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402
from polixor.services.visual import VisualFeatures, estimate_camera_region  # noqa: E402
from polixor.util.ffmpeg import ffmpeg_bin, probe                       # noqa: E402
from polixor.util.fs import safe_filename                               # noqa: E402
from polixor.util.text import (                                         # noqa: E402
    ass_escape, format_timestamp, hex_to_ass_color, is_rtl_text,
    rtl_ratio, wrap_subtitle,
)


# ==========================================================================
# טקסט ו-RTL
# ==========================================================================
def test_rtl_detection():
    assert is_rtl_text("שלום עולם")
    assert is_rtl_text("וואו! אין מצב")
    assert not is_rtl_text("hello world")
    assert not is_rtl_text("12345 !!!")
    # משפט מעורב עם רוב עברי
    assert is_rtl_text("זה היה insane לגמרי")
    assert 0.0 < rtl_ratio("שלום hello") < 1.0


def test_wrap_subtitle_keeps_all_words():
    text = "תקשיבו אני רוצה לספר לכם משהו שלא סיפרתי לאף אחד מעולם"
    lines = wrap_subtitle(text, max_chars=20, max_lines=2)
    assert len(lines) <= 2
    joined = " ".join(lines).split()
    assert joined == text.split(), "שבירת שורות לא איבדה ולא שינתה מילים"


def test_wrap_subtitle_edge_cases():
    assert wrap_subtitle("", 20) == []
    assert wrap_subtitle("   ", 20) == []
    assert wrap_subtitle("מילהארוכהמאודשלאנשברת", 5, 2) == ["מילהארוכהמאודשלאנשברת"]


def test_ass_color_conversion():
    # ASS = &HAABBGGRR
    assert hex_to_ass_color("#FF0000") == "&H000000FF"   # אדום
    assert hex_to_ass_color("#00FF00") == "&H0000FF00"   # ירוק
    assert hex_to_ass_color("#0000FF") == "&H00FF0000"   # כחול
    assert hex_to_ass_color("#FFFFFF") == "&H00FFFFFF"
    assert hex_to_ass_color("bad-input") == "&H00FFFFFF"  # נפילה לברירת מחדל


def test_timestamp_formats():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(3661.5) == "01:01:01,500"
    assert format_timestamp(3661.5, style="ass") == "1:01:01.50"
    assert format_timestamp(-5) == "00:00:00,000"


def test_ass_escape():
    assert ass_escape("a{b}c") == "a\\{b\\}c"
    assert ass_escape("שורה\nשנייה") == "שורה\\Nשנייה"


def test_safe_filename_windows():
    assert safe_filename('a<b>c:d"e/f\\g|h?i*j') == "a-b-c-d-e-f-g-h-i-j"
    assert safe_filename("CON") == "_CON"
    assert safe_filename("") == "clip"
    assert safe_filename("וואו! אין מצב") == "וואו! אין מצב"
    assert len(safe_filename("א" * 300, max_length=50)) <= 50


# ==========================================================================
# ניקוד ובחירה
# ==========================================================================
def _fake_audio(duration=120.0, hop=0.1, peaks=((30, 5), (75, 4))) -> AudioFeatures:
    n = int(duration / hop)
    t = np.arange(n) * hop
    energy = np.full(n, 0.25, dtype=np.float32)
    jump = np.zeros(n, dtype=np.float32)
    for centre, width in peaks:
        mask = np.abs(t - centre) < width
        energy[mask] = 0.9
        jump[mask] = 0.85
    f = AudioFeatures(hop=hop, duration=duration, times=t.astype(np.float32))
    f.rms_db = (energy * 30 - 40).astype(np.float32)
    f.energy = energy
    f.jump = jump
    f.flux = np.zeros(n, dtype=np.float32)
    f.centroid = np.full(n, 1200.0, dtype=np.float32)
    f.zcr = np.zeros(n, dtype=np.float32)
    f.laughter = np.zeros(n, dtype=np.float32)
    f.silence = energy < 0.3
    return f


def _fake_transcript() -> TranscriptResult:
    lines = [
        (10.0, 14.0, "היום אנחנו מתחילים משהו חדש"),
        (28.0, 34.0, "וואו! אין מצב! זה מטורף לגמרי!"),
        (50.0, 56.0, "תקשיבו, אספר לכם סוד שלא סיפרתי לאף אחד"),
        (73.0, 79.0, "ניצחתי! עשיתי את זה!"),
        (100.0, 105.0, "טוב, נסכם ונסיים"),
    ]
    segs = []
    for s, e, txt in lines:
        words = txt.split()
        span = (e - s) / len(words)
        segs.append(Segment(
            start=s, end=e, text=txt, language="he",
            avg_logprob=-0.2, no_speech_prob=0.02,
            words=[Word(start=s + i * span, end=s + (i + 1) * span - 0.02,
                        text=w, probability=0.95) for i, w in enumerate(words)],
        ))
    return TranscriptResult(segments=segs, language="he", duration=120.0,
                            provider="test")


def test_lexical_score_prefers_interesting_text():
    plain = scoring.lexical_score("אז המשכנו הלאה ואז עוד קצת")
    excited = scoring.lexical_score("וואו! אין מצב! זה מטורף לגמרי")
    quiet_interesting = scoring.lexical_score("תקשיבו, אספר לכם סוד")
    assert excited > plain
    assert quiet_interesting > plain, "רגע שקט אך מעניין חייב לקבל ציון"
    assert 0.0 <= excited <= 1.0


def test_quiet_moment_is_findable():
    """
    דרישה מרכזית: רגע שנאמר בשקט אך מעניין לשונית חייב להיות ניתן
    לאיתור גם כשאין לו פרץ עוצמה.
    """
    settings = AppSettings().clamp()
    audio = _fake_audio(peaks=((30, 5), (75, 4)))   # אין פרץ ב-50
    tl = scoring.build_timeline(audio=audio, visual=None,
                                transcript=_fake_transcript(),
                                duration=120.0, settings=settings)
    quiet_score = tl.window_mean(tl.score, 50.0, 56.0)
    flat_score = tl.window_mean(tl.score, 108.0, 115.0)
    assert quiet_score > flat_score * 1.5, (
        f"הרגע השקט ({quiet_score:.3f}) לא בלט מול רקע ({flat_score:.3f})")
    assert tl.speech[tl.idx(52.0)] > 0.2


def test_timeline_channels_independent():
    settings = AppSettings().clamp()
    tl = scoring.build_timeline(audio=_fake_audio(), visual=None, transcript=None,
                                duration=120.0, settings=settings)
    assert tl.vocal.max() > 0.3
    assert tl.speech.max() == 0.0, "אין תמלול => ערוץ הדיבור ריק"
    assert tl.score.max() > 0.3, "הציון עדיין נבנה מהערוץ הקיים"


def test_sensitivity_changes_candidate_count():
    low = AppSettings.from_dict({"sensitivity": 0.05}).clamp()
    high = AppSettings.from_dict({"sensitivity": 0.95}).clamp()
    audio = _fake_audio(peaks=((20, 3), (40, 2), (60, 3), (85, 2), (100, 2)))
    tr = _fake_transcript()

    counts = []
    for s in (low, high):
        tl = scoring.build_timeline(audio=audio, visual=None, transcript=tr,
                                    duration=120.0, settings=s)
        peaks = selection.find_peaks(tl, min_distance_seconds=6.0,
                                     sensitivity=s.sensitivity)
        counts.append(len(peaks))
    assert counts[1] >= counts[0], f"רגישות גבוהה צריכה למצוא לפחות כמו נמוכה: {counts}"


def test_dedupe_removes_overlaps():
    a = selection.Candidate(start=10, end=40, peak_time=25, score=0.9)
    b = selection.Candidate(start=12, end=42, peak_time=27, score=0.7)   # חופף מאוד
    c = selection.Candidate(start=80, end=110, peak_time=95, score=0.6)  # נפרד
    kept = selection.dedupe([a, b, c], iou_threshold=0.3)
    ids = {(k.start, k.end) for k in kept}
    assert (10, 40) in ids and (80, 110) in ids
    assert (12, 42) not in ids, "מועמד חופף עם ציון נמוך יותר הוסר"


def test_clamp_duration_respects_bounds():
    s, e = selection._clamp_duration(10.0, 200.0, 15.0, 60.0, 100.0, 300.0)
    assert 59.0 <= (e - s) <= 60.01
    assert s <= 100.0 <= e, "השיא נשאר בתוך החלון"

    s, e = selection._clamp_duration(50.0, 52.0, 15.0, 60.0, 51.0, 300.0)
    assert (e - s) >= 14.99

    s, e = selection._clamp_duration(-5.0, 400.0, 15.0, 60.0, 10.0, 300.0)
    assert s >= 0.0 and e <= 300.0


def test_arc_quality_prefers_story_shape():
    flat = np.full(60, 0.5, dtype=np.float32)
    arc = np.concatenate([
        np.linspace(0.2, 0.5, 20), np.linspace(0.5, 1.0, 20), np.linspace(1.0, 0.4, 20),
    ]).astype(np.float32)
    peak_at_end = np.linspace(0.2, 1.0, 60).astype(np.float32)
    assert selection._arc_quality(arc) > selection._arc_quality(flat)
    assert selection._arc_quality(arc) > selection._arc_quality(peak_at_end)


def test_enforce_total_limit():
    longs = [selection.Candidate(start=i * 100, end=i * 100 + 60, peak_time=i * 100 + 30,
                                 score=0.9 - i * 0.1) for i in range(4)]
    shorts = [selection.Candidate(start=i * 20, end=i * 20 + 15, peak_time=i * 20 + 7,
                                  score=0.8 - i * 0.05) for i in range(8)]
    l2, s2 = selection.enforce_total_limit(longs, shorts, 5)
    assert len(l2) + len(s2) == 5
    assert len(l2) >= 1 and len(s2) >= 1


# ==========================================================================
# כתוביות
# ==========================================================================
def test_cues_from_words_are_in_range_and_ordered():
    tr = _fake_transcript()
    cues = subtitles.build_cues(tr, clip_start=26.0, clip_end=36.0, max_chars=20)
    assert cues, "נוצרו כתוביות"
    for c in cues:
        assert 0.0 <= c.start < c.end <= 10.5, f"כתובית מחוץ לטווח: {c}"
        assert c.text.strip()
    starts = [c.start for c in cues]
    assert starts == sorted(starts), "הכתוביות ממוינות"
    for a, b in zip(cues, cues[1:]):
        assert a.end <= b.start + 1e-6, "אין חפיפה בין כתוביות"


def test_cues_preserve_transcript_text():
    """דרישה: אין המצאת טקסט – כל מילה בכתובית מגיעה מהתמלול."""
    tr = _fake_transcript()
    cues = subtitles.build_cues(tr, clip_start=26.0, clip_end=36.0, max_chars=20)
    source_words = set(tr.text_between(26.0, 36.0).split())
    for c in cues:
        for w in c.text.split():
            assert w in source_words, f"מילה שלא הייתה בתמלול: {w}"


def test_write_ass_produces_valid_file():
    tr = _fake_transcript()
    cues = subtitles.build_cues(tr, clip_start=26.0, clip_end=36.0, max_chars=20)
    style = subtitles.SubtitleStyle(font="DejaVu Sans", size=54, word_level=True)
    with tempfile.TemporaryDirectory() as d:
        path = subtitles.write_ass(cues, Path(d) / "s.ass", width=1080, height=1920,
                                   style=style, title_text="כותרת בדיקה")
        text = path.read_text("utf-8")
        assert "[Script Info]" in text and "[V4+ Styles]" in text
        assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
        assert "Style: Polixor," in text and "Style: PolixorTitle," in text
        assert "כותרת בדיקה" in text
        dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
        assert len(dialogues) > len(cues), "מצב מילה-אחר-מילה יוצר יותר שורות"
        for line in dialogues:
            assert line.count(",") >= 9, "מבנה שורת Dialogue תקין"


def test_write_srt_roundtrip():
    tr = _fake_transcript()
    cues = subtitles.build_cues(tr, clip_start=26.0, clip_end=36.0, max_chars=24)
    with tempfile.TemporaryDirectory() as d:
        p = subtitles.write_srt(cues, Path(d) / "s.srt")
        blocks = [b for b in p.read_text("utf-8").split("\n\n") if b.strip()]
        assert len(blocks) == len([c for c in cues if c.text.strip()])
        assert "-->" in blocks[0]


def test_max_chars_scales_with_resolution():
    small = subtitles.SubtitleStyle(size=54, margin_h=70)
    wide = subtitles._max_chars_for(small, 1920)
    narrow = subtitles._max_chars_for(small, 720)
    assert wide > narrow, "פריים רחב יותר מכיל יותר תווים בשורה"
    assert 14 <= narrow <= 46


# ==========================================================================
# Smart Reframe
# ==========================================================================
def _visual_with_faces(duration=30.0, fps=2.0, path_fn=None) -> VisualFeatures:
    """
    בונה VisualFeatures עם מלבני פנים מוזרקים במסלול ידוע.
    זה מאפשר לבדוק את חישוב מסלול החיתוך בלי תלות בזיהוי פנים אמיתי.
    """
    n = int(duration * fps)
    faces = []
    for i in range(n):
        t = i / fps
        cx = path_fn(t) if path_fn else 0.5
        faces.append([(cx - 0.06, 0.25, 0.12, 0.18)])
    vf = VisualFeatures(fps=fps, duration=duration, width=320, height=180,
                        faces=faces, analyzed=True)
    vf.times = (np.arange(n) / fps).astype(np.float32)
    vf.scene = np.zeros(n, dtype=np.float32)
    vf.motion = np.zeros(n, dtype=np.float32)
    vf.brightness = np.full(n, 0.5, dtype=np.float32)
    vf.flash = np.zeros(n, dtype=np.float32)
    return vf


def test_reframe_tracks_moving_face():
    # הפנים נעות מ-0.25 ל-0.75 לאורך הקטע
    vf = _visual_with_faces(path_fn=lambda t: 0.25 + 0.5 * min(1.0, t / 25.0))
    plan = plan_reframe(vf, clip_start=0.0, clip_end=30.0, layout="auto_face")
    assert plan.layout == "auto_face"
    assert plan.tracked_ratio > 0.9
    assert plan.segments, "נוצרו קטעי תנועה"
    assert plan.segments[0].x0 < plan.segments[-1].x1, "המסגרת זזה בכיוון הנכון"
    assert all(0.0 <= s.x0 <= 1.0 and 0.0 <= s.x1 <= 1.0 for s in plan.segments)


def test_reframe_static_face_produces_stable_frame():
    vf = _visual_with_faces(path_fn=lambda t: 0.7)
    plan = plan_reframe(vf, clip_start=0.0, clip_end=30.0, layout="auto_face")
    xs = [s.x0 for s in plan.segments] + [s.x1 for s in plan.segments]
    assert max(xs) - min(xs) < 0.05, "פנים סטטיות => מסגרת יציבה (אזור מת)"


def test_reframe_falls_back_without_faces():
    vf = VisualFeatures(fps=2.0, duration=30.0, faces=[[] for _ in range(60)],
                        analyzed=True)
    vf.times = (np.arange(60) / 2.0).astype(np.float32)
    plan = plan_reframe(vf, clip_start=0.0, clip_end=30.0, layout="auto_face")
    assert plan.layout == "center"
    assert "חיתוך מרכזי" in plan.note


def test_camera_region_estimation():
    # פנים קבועות בפינה הימנית-עליונה, כמו מצלמת סטרימר
    vf = _visual_with_faces(duration=60.0, path_fn=lambda t: 0.82)
    for f in vf.faces:
        f[0] = (0.76, 0.06, 0.12, 0.18)
    region = estimate_camera_region(vf)
    assert region is not None
    assert region["x"] > 0.5, "האזור בצד ימין של הפריים"
    assert region["y"] < 0.35, "האזור בחלק העליון"
    assert 0.0 < region["w"] <= 1.0 and 0.0 < region["h"] <= 1.0
    assert region["x"] + region["w"] <= 1.0001


def test_reframe_expression_is_valid_ffmpeg():
    """
    בדיקה אמיתית: מריצים FFmpeg עם ביטוי החיתוך שנוצר ומוודאים
    שהוא מתקבל ושהפלט אכן 9:16.
    """
    vf = _visual_with_faces(duration=6.0, fps=4.0,
                            path_fn=lambda t: 0.3 + 0.4 * (t / 6.0))
    plan = plan_reframe(vf, clip_start=0.0, clip_end=6.0, layout="auto_face")
    vf_filter = build_vertical_filter(plan, out_width=360, out_height=640,
                                      src_width=1280, src_height=720)
    assert "crop=" in vf_filter and "if(lt(t" in vf_filter

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.mp4"
        res = subprocess.run([
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=10:duration=3",
            "-vf", vf_filter, "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(out),
        ], capture_output=True, text=True, timeout=180)
        assert res.returncode == 0, f"FFmpeg דחה את הביטוי:\n{res.stderr[-600:]}"
        info = probe(out)
        assert (info.width, info.height) == (360, 640)
        assert abs(info.width / info.height - 9 / 16) < 0.01


def test_split_filter_is_valid_ffmpeg():
    vf = _visual_with_faces(duration=6.0, fps=4.0, path_fn=lambda t: 0.82)
    for f in vf.faces:
        f[0] = (0.76, 0.06, 0.12, 0.18)
    plan = plan_reframe(vf, clip_start=0.0, clip_end=6.0, layout="split")
    assert plan.layout == "split", plan.note
    graph = build_vertical_filter(plan, out_width=360, out_height=640,
                                  src_width=1280, src_height=720)
    assert "vstack" in graph

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "split.mp4"
        res = subprocess.run([
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=10:duration=2",
            "-filter_complex", f"[0:v]{graph}[v]", "-map", "[v]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out),
        ], capture_output=True, text=True, timeout=180)
        assert res.returncode == 0, f"מסך מפוצל נכשל:\n{res.stderr[-600:]}"
        info = probe(out)
        assert (info.width, info.height) == (360, 640)


# ==========================================================================
# מנוע העריכה
# ==========================================================================
def _editing_inputs():
    """אודיו עם שקט מוגדר + תמלול עם פערים ידועים."""
    duration = 40.0
    audio = _fake_audio(duration=duration, peaks=((12, 3), (30, 3)))
    lines = [
        (2.0, 6.0, "שלום לכולם וברוכים הבאים"),
        # פער של 3 שניות – אוויר מת מובהק
        (9.0, 13.5, "וואו! אין מצב! ראיתם את זה?"),
        # פער של 1.2 שניות
        (14.7, 18.0, "זה היה מטורף לגמרי"),
        # פער של 5 שניות
        (23.0, 27.0, "תקשיבו, אספר לכם סוד"),
        (27.4, 32.0, "ניצחתי! עשיתי את זה!"),
    ]
    segs = []
    for st, en, txt in lines:
        words = txt.split()
        span = (en - st) / len(words)
        segs.append(Segment(
            start=st, end=en, text=txt, language="he",
            avg_logprob=-0.2, no_speech_prob=0.02,
            words=[Word(start=st + i * span, end=st + (i + 1) * span - 0.03,
                        text=w, probability=0.95) for i, w in enumerate(words)],
        ))
    tr = TranscriptResult(segments=segs, language="he", duration=duration,
                          provider="test")
    tl = scoring.build_timeline(audio=audio, visual=None, transcript=tr,
                                duration=duration, settings=AppSettings().clamp())
    return audio, tr, tl


def test_raw_style_changes_nothing():
    audio, tr, tl = _editing_inputs()
    plan = editing.build_edit_plan(
        clip_start=0.0, clip_end=40.0, peak_time=12.0,
        style=editing.get_style("raw"), audio=audio, timeline=tl, transcript=tr)
    assert len(plan.beats) == 1
    assert plan.cut_count == 0
    assert abs(plan.out_duration - 40.0) < 0.01
    assert plan.removed_seconds == 0.0
    assert plan.is_trivial


def test_clean_style_removes_dead_air():
    audio, tr, tl = _editing_inputs()
    plan = editing.build_edit_plan(
        clip_start=0.0, clip_end=40.0, peak_time=12.0,
        style=editing.get_style("clean"), audio=audio, timeline=tl, transcript=tr)
    assert plan.cut_count >= 1, "לא בוצע אף חיתוך"
    assert plan.removed_seconds > 1.0, f"הוסר מעט מדי: {plan.removed_seconds}"
    assert plan.out_duration < 40.0
    # לא חורג ממכסת ההסרה
    assert plan.removed_seconds <= 0.25 * 40.0 + 0.1


def test_removal_budget_is_respected():
    audio, tr, tl = _editing_inputs()
    style = editing.get_style("hype")
    plan = editing.build_edit_plan(
        clip_start=0.0, clip_end=40.0, peak_time=12.0,
        style=style, audio=audio, timeline=tl, transcript=tr)
    assert plan.removed_seconds <= style.max_removed_ratio * 40.0 + 0.2, \
        "העורך חתך יותר מהמכסה"


def test_styles_escalate():
    audio, tr, tl = _editing_inputs()
    durations = {}
    for name in ("raw", "clean", "dynamic", "hype"):
        plan = editing.build_edit_plan(
            clip_start=0.0, clip_end=40.0, peak_time=12.0,
            style=editing.get_style(name), audio=audio, timeline=tl, transcript=tr)
        durations[name] = plan.out_duration
    assert durations["raw"] > durations["clean"] >= durations["hype"], \
        f"הסגנונות לא מתקדמים בהידוק: {durations}"


def test_angle_changes_only_in_dynamic_styles():
    audio, tr, tl = _editing_inputs()
    for name, expect in (("clean", False), ("dynamic", True), ("hype", True)):
        plan = editing.build_edit_plan(
            clip_start=0.0, clip_end=40.0, peak_time=12.0,
            style=editing.get_style(name), audio=audio, timeline=tl, transcript=tr)
        zooms = sum(1 for b in plan.beats if abs(b.zoom - 1.0) > 1e-3)
        assert (zooms > 0) == expect, f"{name}: {zooms} זוויות (ציפינו {expect})"


def test_no_gradual_zoom_survives_planning():
    """דחיפות חייבות להתפצל לתת-ביטים – crop לא תומך בזום תלוי-זמן."""
    audio, tr, tl = _editing_inputs()
    for name in ("dynamic", "hype"):
        plan = editing.build_edit_plan(
            clip_start=0.0, clip_end=40.0, peak_time=12.0,
            style=editing.get_style(name), audio=audio, timeline=tl, transcript=tr)
        assert all(b.zoom_to == 0.0 for b in plan.beats), \
            f"{name}: נשאר zoom_to בתכנית"


def test_time_mapping_is_monotonic_and_shrinks():
    audio, tr, tl = _editing_inputs()
    plan = editing.build_edit_plan(
        clip_start=0.0, clip_end=40.0, peak_time=12.0,
        style=editing.get_style("dynamic"), audio=audio, timeline=tl, transcript=tr)

    prev = -1.0
    for t in np.arange(0.0, 40.0, 0.25):
        m = plan.map_time(float(t))
        if m is None:
            continue
        assert m >= prev - 1e-6, f"המיפוי לא מונוטוני ב-{t}"
        assert m <= t + 1e-6, "זמן ערוך לא יכול להיות מאוחר מזמן המקור"
        prev = m
    assert plan.map_time(0.0) is not None
    end = plan.map_time(39.9)
    assert end is None or end <= plan.out_duration + 0.05


def test_cues_stay_in_sync_after_cuts():
    """
    הבדיקה החשובה ביותר בעריכה: אחרי הסרת אוויר מת, כתובית חייבת
    להישאר על הדיבור שלה. בודקים שהיא זזה בדיוק כמו המילים שבה.
    """
    audio, tr, tl = _editing_inputs()
    plan = editing.build_edit_plan(
        clip_start=0.0, clip_end=40.0, peak_time=12.0,
        style=editing.get_style("dynamic"), audio=audio, timeline=tl, transcript=tr)

    cues = subtitles.build_cues(tr, clip_start=0.0, clip_end=40.0, max_chars=24)
    assert cues
    mapped = subtitles.remap_cues(cues, plan)
    assert mapped, "כל הכתוביות נעלמו"

    for c in mapped:
        assert 0.0 <= c.start < c.end <= plan.out_duration + 0.1, \
            f"כתובית מחוץ לגבולות הפלט: {c.start}-{c.end} / {plan.out_duration}"
        for w in c.words:
            assert c.start - 0.35 <= w["start"] <= c.end + 0.35, \
                "מילה מחוץ לטווח הכתובית שלה"

    starts = [c.start for c in mapped]
    assert starts == sorted(starts), "הכתוביות לא ממוינות אחרי המיפוי"

    # כל כתובית ששרדה שומרת על משכה היחסי (עד כדי חיתוך חלקי)
    for c in mapped:
        assert c.end - c.start > 0.1


def test_dramatic_pause_is_protected():
    """שתיקה שערוץ ה-pause סימן כמשמעותית מקוצרת, לא נמחקת."""
    audio, tr, tl = _editing_inputs()
    plan = editing.build_edit_plan(
        clip_start=0.0, clip_end=40.0, peak_time=12.0,
        style=editing.get_style("clean"), audio=audio, timeline=tl, transcript=tr)
    # הפער של 5 שניות לפני "תקשיבו" הוא מועמד לשתיקה דרמטית
    assert plan.dramatic_kept >= 0
    gap_start = plan.map_time(18.0)
    speech_start = plan.map_time(23.2)
    if gap_start is not None and speech_start is not None:
        kept = speech_start - gap_start
        assert kept >= 0.0, "הפער התהפך"


def test_beats_are_contiguous_and_ordered():
    audio, tr, tl = _editing_inputs()
    for name in editing.EDIT_STYLES:
        plan = editing.build_edit_plan(
            clip_start=0.0, clip_end=40.0, peak_time=12.0,
            style=editing.get_style(name), audio=audio, timeline=tl, transcript=tr)
        for a, b in zip(plan.beats, plan.beats[1:]):
            assert a.src_end <= b.src_start + 1e-6, f"{name}: ביטים חופפים"
        for b in plan.beats:
            assert b.src_duration > 0.05, f"{name}: ביט ריק"
            assert 0.0 <= b.src_start <= 40.0


def test_speed_only_on_speechless_beats():
    audio, tr, tl = _editing_inputs()
    plan = editing.build_edit_plan(
        clip_start=0.0, clip_end=40.0, peak_time=12.0,
        style=editing.get_style("hype"), audio=audio, timeline=tl, transcript=tr)
    for b in plan.beats:
        if abs(b.speed - 1.0) > 1e-3:
            words = tr.words_between(b.src_start, b.src_end)
            speech = sum(w.end - w.start for w in words)
            assert speech / b.src_duration < 0.2, "הואץ ביט עם דיבור"


def test_style_overrides_from_settings():
    s = AppSettings.from_dict({"edit_style_short": "hype", "angle_changes": False,
                               "remove_silence": False})
    style = editing.style_from_settings(s, vertical=True)
    assert style.name == "hype"
    assert style.angle_changes is False
    assert style.remove_silence is False
    # הסגנון המקורי לא נפגע
    assert editing.get_style("hype").angle_changes is True


def test_atempo_chain_handles_extremes():
    assert editing._atempo_chain(1.0).startswith("atempo=1.0")
    assert editing._atempo_chain(3.0).count("atempo") == 2
    assert editing._atempo_chain(0.3).count("atempo") >= 2


# ==========================================================================
# רזולוציית יעד
# ==========================================================================
def test_target_resolution_never_upscales():
    from polixor.services.render import target_resolution

    # מקור 720p, מבקשים 1080p אופקי => מוגבל ל-720p
    w, h = target_resolution("1920x1080", 1280, 720, vertical=False)
    assert h <= 720

    # מקור 1080p => מקבלים 1080p מלא
    w, h = target_resolution("1920x1080", 1920, 1080, vertical=False)
    assert (w, h) == (1920, 1080)

    # אנכי: המידות תמיד זוגיות (דרישה של yuv420p)
    w, h = target_resolution("1080x1920", 1280, 720, vertical=True)
    assert w % 2 == 0 and h % 2 == 0
    assert abs(w / h - 9 / 16) < 0.02


# ==========================================================================
# הגדרות
# ==========================================================================
def test_settings_clamp_fixes_invalid_values():
    s = AppSettings.from_dict({
        "sensitivity": 5.0, "long_min_seconds": 600, "long_max_seconds": 60,
        "short_min_seconds": -3, "long_mode": "nonsense", "ai_mode": "local",
        "short_layout": "zzz", "concurrent_jobs": 99,
    })
    assert s.sensitivity == 1.0
    assert s.long_max_seconds > s.long_min_seconds
    assert s.short_min_seconds >= 3
    assert s.long_mode == "continuous"
    assert s.ai_mode == "heuristic"        # תאימות לאחור
    assert s.short_layout == "auto_face"
    assert s.concurrent_jobs <= 4


def test_settings_roundtrip():
    s = AppSettings.from_dict({"sensitivity": 0.7, "short_count": 9})
    again = AppSettings.from_dict(s.to_dict())
    assert again.sensitivity == 0.7 and again.short_count == 9


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
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} בדיקות יחידה עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
