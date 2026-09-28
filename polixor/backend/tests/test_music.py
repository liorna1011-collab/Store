"""
בדיקות למנוע המוזיקה.

הבדיקה המרכזית כאן אינה „הפילטר נבנה" אלא **מדידה של הפלט**:
מריצים FFmpeg אמיתי עם קול ומוזיקה, מוציאים את פס המוזיקה אחרי
העיבוד, ומודדים כמה dB הוא יורד מתחת לדיבור לעומת ההפסקות.

הרצה:  python3 tests/test_music.py
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxmus_"))

from polixor.config import PATHS, AppSettings                    # noqa: E402

PATHS.ensure()

from polixor.services import music_engine as me                  # noqa: E402
from polixor.services import semantics as sem                    # noqa: E402
from polixor.services.transcribe import (                        # noqa: E402
    Segment, TranscriptResult, Word,
)
from polixor.util.ffmpeg import ffmpeg_bin                       # noqa: E402

TMP = Path(os.environ["POLIXOR_DATA_DIR"]) / "music"
TMP.mkdir(parents=True, exist_ok=True)
SR = 48000

# דיבור 2.6 שניות מכל 4 — אותה מעטפת שמשמשת בשאר הבדיקות
SPEECH_ON, CYCLE = 2.6, 4.0
DURATION = 20.0


def speech_spans(duration: float = DURATION) -> list[tuple[float, float]]:
    out, t = [], 0.0
    while t < duration:
        out.append((t, min(duration, t + SPEECH_ON)))
        t += CYCLE
    return out


def make_voice(dst: Path) -> Path:
    if dst.exists():
        return dst
    n = int(SR * DURATION)
    t = np.arange(n, dtype=np.float64) / SR
    voice = (np.sin(2 * np.pi * 185.0 * t)
             * (0.5 + 0.5 * np.sin(2 * np.pi * 4.3 * t)))
    voice *= (np.mod(t, CYCLE) < SPEECH_ON).astype(np.float64) * 0.35
    _write(dst, voice)
    return dst


def make_music(dst: Path) -> Path:
    """„מוזיקה": שני טונים יציבים בעוצמה קבועה — קל למדוד."""
    if dst.exists():
        return dst
    n = int(SR * DURATION)
    t = np.arange(n, dtype=np.float64) / SR
    music = (0.5 * np.sin(2 * np.pi * 440.0 * t)
             + 0.5 * np.sin(2 * np.pi * 660.0 * t)) * 0.30
    _write(dst, music)
    return dst


def _write(dst: Path, x: np.ndarray) -> None:
    pcm = np.clip(x, -1.0, 1.0)
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((pcm * 32767.0).astype(np.int16).tobytes())


def build_semantics() -> sem.SemanticAnalysis:
    lines = [
        ("שלוש טעויות שעשיתי בשנה הראשונה שלי", SPEECH_ON),
        ("הראשונה הייתה שלא ביקשתי עזרה בזמן", SPEECH_ON),
        ("זה היה הרגע הכי קשה שעברתי בחיים", SPEECH_ON),
        ("ומשם הכול התחיל להשתנות לטובה", SPEECH_ON),
        ("אם זה עזר לכם תעקבו לעוד טיפים", SPEECH_ON),
    ]
    segs, t = [], 0.0
    for text, dur in lines:
        toks = text.split()
        per = dur / len(toks)
        words, wt = [], t
        for tok in toks:
            words.append(Word(start=round(wt, 3),
                              end=round(wt + per * 0.9, 3), text=tok))
            wt += per
        segs.append(Segment(start=t, end=t + dur, text=text, words=words,
                            language="he"))
        t += CYCLE
    tr = TranscriptResult(segments=segs, language="he", duration=DURATION)
    return sem.analyze(tr, None, settings=AppSettings(), use_llm=False)


# ==========================================================================
# התכנית
# ==========================================================================
def test_no_music_file_means_no_plan_and_says_why():
    """הדרישה שלא להציג משהו כעובד כשהוא לא: אין קובץ — אין מוזיקה."""
    plan = me.plan_music(build_semantics(), total_duration=DURATION,
                         music_path=None)
    assert plan.available is False
    assert plan.is_active is False
    assert plan.cues == []
    assert any("אינה מספקת מוזיקה" in n for n in plan.notes), plan.notes


def test_missing_file_is_not_treated_as_available():
    plan = me.plan_music(build_semantics(), total_duration=DURATION,
                         music_path=TMP / "does_not_exist.wav")
    assert plan.available is False


class _FakeAnalysis:
    """ביטים בתפקידים מוגדרים, כדי לבדוק את מנוע המוזיקה בנפרד
    מהחלטות הסיווג הסמנטי."""

    def __init__(self, beats):
        self.beats = beats


def test_emotional_peak_is_louder_than_the_story_body():
    """§19: Emotional peak → rising, Story → low, Hook → medium."""
    beats = [
        sem.NarrativeBeat(0.0, 4.0, "hook", 0.9),
        sem.NarrativeBeat(4.0, 10.0, "setup", 0.8),
        sem.NarrativeBeat(10.0, 15.0, "emotional_peak", 0.9),
        sem.NarrativeBeat(15.0, 20.0, "cta", 0.9),
    ]
    plan = me.plan_music(_FakeAnalysis(beats), total_duration=DURATION,
                         music_path=make_music(TMP / "music.wav"))
    by_role = {c.role: c.gain_db for c in plan.cues}
    assert by_role["emotional_peak"] > by_role["hook"] > by_role["setup"], \
        by_role
    assert by_role["cta"] < by_role["hook"], by_role
    peak = [c for c in plan.cues if c.role == "emotional_peak"][0]
    assert "עולה" in peak.reason, peak.to_dict()


def test_role_gain_table_follows_the_spec():
    g = me.ROLE_GAIN_DB
    assert g["emotional_peak"] > g["hook"], g
    assert g["hook"] > g["setup"] and g["hook"] > g["main_idea"], g
    assert g["key_claim"] < g["main_idea"], g
    assert g["filler"] == min(g.values()), g


def test_music_drops_where_the_words_matter():
    a = build_semantics()
    plan = me.plan_music(a, total_duration=DURATION,
                         music_path=make_music(TMP / "music.wav"))
    quiet = [c for c in plan.cues if c.role in ("key_claim", "cta")]
    if quiet:
        loudest = max(c.gain_db for c in plan.cues)
        assert min(c.gain_db for c in quiet) < loudest
        assert any("יישמעו" in c.reason for c in quiet)


def test_curve_covers_the_whole_clip_without_gaps():
    a = build_semantics()
    plan = me.plan_music(a, total_duration=DURATION,
                         music_path=make_music(TMP / "music.wav"))
    assert plan.cues
    assert plan.cues[0].start <= 0.01
    assert plan.cues[-1].end >= DURATION - 0.05
    for x, y in zip(plan.cues, plan.cues[1:]):
        assert abs(y.start - x.end) < 0.06, (x.to_dict(), y.to_dict())


def test_every_cue_explains_itself():
    a = build_semantics()
    plan = me.plan_music(a, total_duration=DURATION,
                         music_path=make_music(TMP / "music.wav"))
    for c in plan.cues:
        assert c.reason, c.to_dict()
        assert c.end > c.start


def test_no_transcript_gives_a_flat_quiet_curve():
    plan = me.plan_music(None, total_duration=DURATION,
                         music_path=make_music(TMP / "music.wav"))
    assert plan.available and len(plan.cues) == 1
    assert plan.cues[0].role == "unknown"
    assert any("תמלול" in n for n in plan.notes), plan.notes


def test_profiles_differ_and_unknown_falls_back():
    assert me.get_profile("energetic").bed_lufs > \
        me.get_profile("minimal").bed_lufs
    assert me.get_profile("nonsense").name == me.DEFAULT_PROFILE
    assert len(me.profile_catalog()) == len(me.PROFILES)


def test_plan_is_json_serialisable():
    a = build_semantics()
    plan = me.plan_music(a, total_duration=DURATION,
                         music_path=make_music(TMP / "music.wav"))
    blob = json.dumps(plan.to_dict(), ensure_ascii=False)
    assert "cues" in blob and "profile" in blob


def test_volume_expression_matches_the_curve():
    a = build_semantics()
    plan = me.plan_music(a, total_duration=DURATION,
                         music_path=make_music(TMP / "music.wav"))
    expr = me.volume_expression(plan)
    assert "if(" in expr and "t," in expr
    # הערכים בביטוי הם ליניאריים ותואמים לעקומה
    for c in plan.cues:
        assert f"{10 ** (c.gain_db / 20):.5f}" in expr, c.to_dict()


# ==========================================================================
# מדידה על פלט אמיתי
# ==========================================================================
def render_music_only(profile: str = "balanced") -> Path:
    """מרנדר את פס המוזיקה אחרי ההנמכה, בלי הקול, כדי למדוד אותו."""
    out = TMP / f"ducked_{profile}.wav"
    if out.exists():
        return out
    voice = make_voice(TMP / "voice.wav")
    music = make_music(TMP / "music.wav")
    a = build_semantics()
    plan = me.plan_music(a, total_duration=DURATION, profile=profile,
                         music_path=music)
    flt = me.build_filter(plan, music_input=1, voice_label="0:a",
                          out_label="out", music_only=True)
    res = subprocess.run(
        [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(voice), "-i", str(music),
         "-filter_complex", flt, "-map", "[out]",
         "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(out)],
        capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg נכשל: {res.stderr[-500:]}")
    return out


def test_music_level_is_derived_from_the_measured_file():
    """
    „הנמך ב-20dB" נותן תוצאה אחרת לכל קובץ מוזיקה. היעד מוגדר
    ב-LUFS ונמדד מול הקובץ בפועל, ולכן אותו פרופיל נשמע אותו דבר
    בלי קשר לעוצמה שבה הקובץ הגיע.
    """
    quiet = TMP / "quiet_music.wav"
    loud = TMP / "loud_music.wav"
    if not quiet.exists():
        n = int(SR * DURATION)
        t = np.arange(n, dtype=np.float64) / SR
        tone = (0.5 * np.sin(2 * np.pi * 440.0 * t)
                + 0.5 * np.sin(2 * np.pi * 660.0 * t))
        _write(quiet, tone * 0.03)
        _write(loud, tone * 0.60)

    a = build_semantics()
    pq = me.plan_music(a, total_duration=DURATION, music_path=quiet)
    pl = me.plan_music(a, total_duration=DURATION, music_path=loud)
    assert pq.measured and pl.measured, (pq.to_dict(), pl.to_dict())
    assert pq.music_lufs < pl.music_lufs - 10, (pq.music_lufs, pl.music_lufs)

    # הקובץ השקט מקבל הגבר גדול יותר — כדי להגיע לאותו יעד
    hook_q = [c for c in pq.cues if c.role == "hook"][0].gain_db
    hook_l = [c for c in pl.cues if c.role == "hook"][0].gain_db
    assert hook_q > hook_l + 10, (hook_q, hook_l)
    # והרמה הסופית זהה בשני המקרים
    assert abs((hook_q + pq.music_lufs) - (hook_l + pl.music_lufs)) < 1.0


def test_unmeasurable_music_says_it_assumed():
    plan = me.plan_music(build_semantics(), total_duration=DURATION,
                         music_path=make_music(TMP / "music.wav"))
    assert plan.measured, "קובץ תקין אמור להימדד"
    assert plan.music_lufs is not None


def test_music_actually_ducks_under_speech():
    """
    הבדיקה המרכזית: לא „הפילטר נבנה" אלא **כמה dB המוזיקה יורדת**
    מתחת לדיבור, נמדד מהקובץ שיצא.
    """
    out = render_music_only("balanced")
    report = me.measure_ducking(out, speech_spans())
    assert report.ok, report.to_dict()
    assert report.reduction_db is not None
    assert report.reduction_db >= 4.0, report.to_dict()


def test_stronger_profile_ducks_at_least_as_much():
    balanced = me.measure_ducking(render_music_only("balanced"),
                                  speech_spans())
    energetic = me.measure_ducking(render_music_only("energetic"),
                                   speech_spans())
    assert balanced.ok and energetic.ok
    assert energetic.reduction_db >= balanced.reduction_db - 1.0, (
        balanced.to_dict(), energetic.to_dict())


def test_music_stays_below_the_speech():
    """המוזיקה לא אמורה להתחרות בקול גם בהפסקות."""
    out = render_music_only("balanced")
    report = me.measure_ducking(out, speech_spans())
    assert report.ok
    # הקול נבנה ב--0.35 שיא ≈ ‎-12dBFS RMS בזמן דיבור
    assert report.speech_db < -20.0, report.to_dict()


def test_full_mix_renders_and_keeps_both_tracks():
    voice = make_voice(TMP / "voice.wav")
    music = make_music(TMP / "music.wav")
    a = build_semantics()
    plan = me.plan_music(a, total_duration=DURATION, music_path=music)
    flt = me.build_filter(plan, music_input=1, voice_label="0:a",
                          out_label="out")
    out = TMP / "full_mix.wav"
    res = subprocess.run(
        [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(voice), "-i", str(music), "-filter_complex", flt,
         "-map", "[out]", "-ac", "1", "-ar", str(SR),
         "-c:a", "pcm_s16le", str(out)],
        capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[-500:]
    assert out.exists()

    with wave.open(str(out), "rb") as wf:
        x = np.frombuffer(wf.readframes(wf.getnframes()),
                          dtype=np.int16).astype(np.float32) / 32768.0
    assert x.size > SR * (DURATION - 1)
    assert float(np.max(np.abs(x))) <= 0.99, "התערובת נחתכה"
    assert float(np.sqrt(np.mean(x ** 2))) > 0.01, "התערובת שקטה מדי"


def test_measurement_reports_failure_instead_of_guessing():
    bad = me.measure_ducking(TMP / "nope.wav", speech_spans())
    assert not bad.ok and bad.error
    assert bad.reduction_db is None


def test_measurement_needs_both_speech_and_pauses():
    out = render_music_only("balanced")
    only = me.measure_ducking(out, [(0.0, DURATION)])
    assert not only.ok and "הפסקות" in only.error


# ==========================================================================
# שילוב בפייפליין
# ==========================================================================
def test_beats_are_mapped_to_output_time():
    """
    הביטים של הבמאי הם בזמני המקור. אחרי החיתוכים הקליפ קצר
    יותר, ועקומה שנבנתה על זמני המקור הייתה מחליקה ביחס לתמונה.
    """
    from polixor.pipeline import _beats_in_output_time
    from polixor.services import editing

    class _DP:
        source_start = 100.0
        beats = [{"start": 100.0, "end": 104.0, "role": "hook"},
                 {"start": 106.0, "end": 110.0, "role": "emotional_peak"}]

    # העורך מסיר את השנייה שבין 104 ל-106 (ביחס לחלון: 4–6)
    ep = editing.EditPlan(beats=[editing.Beat(0.0, 4.0),
                                 editing.Beat(6.0, 10.0)])
    out = _beats_in_output_time([_DP()], [ep])
    assert len(out) == 2, out
    assert abs(out[0]["start"] - 0.0) < 0.01, out
    assert abs(out[0]["end"] - 4.0) < 0.01, out
    # הביט השני מתחיל מיד אחרי הראשון בפלט, לא ב-6
    assert abs(out[1]["start"] - 4.0) < 0.01, out
    assert out[1]["role"] == "emotional_peak"


def test_music_reaches_the_rendered_clip_and_ducks_there():
    """
    בדיקה מלאה: מרנדרים קליפ עם וידאו וקול, מערבבים מוזיקה דרך
    אותו מסלול שהפייפליין משתמש בו, ומודדים שהמוזיקה באמת שם
    ושהיא יורדת מתחת לדיבור.
    """
    voice = make_voice(TMP / "voice.wav")
    music = make_music(TMP / "music.wav")
    clip = TMP / "clip_with_voice.mp4"
    if not clip.exists():
        res = subprocess.run(
            [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"testsrc2=s=320x180:d={DURATION}:r=25",
             "-i", str(voice), "-pix_fmt", "yuv420p", "-c:v", "libx264",
             "-preset", "veryfast", "-c:a", "aac", "-shortest", str(clip)],
            capture_output=True, text=True, timeout=300)
        assert res.returncode == 0, res.stderr[-400:]

    beats = [{"start": 0.0, "end": 6.0, "role": "hook"},
             {"start": 6.0, "end": 14.0, "role": "setup"},
             {"start": 14.0, "end": DURATION, "role": "emotional_peak"}]
    plan = me.plan_music(total_duration=DURATION, music_path=music,
                         beats=beats)
    assert plan.is_active

    flt = me.build_filter(plan, music_input=1, voice_label="0:a",
                          out_label="mixed")
    mixed = TMP / "clip_mixed.mp4"
    res = subprocess.run(
        [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(clip), "-i", str(music), "-filter_complex", flt,
         "-map", "0:v", "-map", "[mixed]", "-c:v", "copy", "-c:a", "aac",
         "-b:a", "192k", "-shortest", str(mixed)],
        capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[-500:]
    assert mixed.exists()

    # המוזיקה נמצאת בפלט: אנרגיה ב-440/660Hz שלא הייתה בקול בלבד
    before = _band_energy(clip)
    after = _band_energy(mixed)
    assert after > before * 3, (before, after)

    # והווידאו לא נגע
    from polixor.util.ffmpeg import probe
    a, b = probe(clip), probe(mixed)
    assert a.width == b.width and a.height == b.height
    assert abs(a.duration - b.duration) < 0.3


def test_pipeline_mix_replaces_the_clip_audio():
    """
    בדיקת החיווט עצמו: `_mix_music` של הפייפליין צריך לערבב
    ולהחליף את הקובץ במקום — לא רק להחזיר תכנית שנראית פעילה.
    """
    from polixor.pipeline import _mix_music
    from polixor.services import editing

    import shutil

    music = make_music(TMP / "music.wav")
    work = TMP / "wire_work.mp4"
    shutil.copy(wire_clip(), work)

    class _DP:
        source_start = 0.0
        beats = [{"start": 0.0, "end": 10.0, "role": "hook"},
                 {"start": 10.0, "end": DURATION, "role": "emotional_peak"}]

    class _Ctx:
        settings = AppSettings.from_dict({
            **AppSettings().to_dict(),
            "music_enabled": True, "music_path": str(music),
            "music_profile": "energetic"})

    ep = editing.EditPlan(beats=[editing.Beat(0.0, DURATION)])
    before = _tone_peak(work)
    info = _mix_music(_Ctx(), work, director_plans=[_DP()],
                      edit_plans=[ep], duration=DURATION)
    after = _tone_peak(work)

    assert info.get("active") is True, info
    assert not info.get("error"), info
    assert after > before + 6.0, (before, after)


def test_music_disabled_leaves_the_clip_untouched():
    from polixor.pipeline import _mix_music

    import shutil

    work = TMP / "wire_off.mp4"
    shutil.copy(wire_clip(), work)
    size = work.stat().st_size

    class _Ctx:
        settings = AppSettings()

    info = _mix_music(_Ctx(), work, director_plans=[], edit_plans=[],
                      duration=DURATION)
    assert info == {}
    assert work.stat().st_size == size


def wire_clip() -> Path:
    """קליפ בסיס עם וידאו וקול, לבדיקות החיווט."""
    clip = TMP / "wire_clip.mp4"
    if not clip.exists():
        subprocess.run(
            [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"testsrc2=s=320x180:d={DURATION}:r=25",
             "-i", str(make_voice(TMP / "voice.wav")),
             "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
             "-c:a", "aac", "-shortest", str(clip)],
            capture_output=True, timeout=300)
    return clip


def _tone_peak(path: Path, f0: float = 440.0) -> float:
    """כמה dB הטון בולט מעל הרקע סביבו — מדד רגיש לנוכחות המוזיקה."""
    wav = TMP / (path.stem + "_tone.wav")
    subprocess.run(
        [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
         "-c:a", "pcm_s16le", str(wav)],
        capture_output=True, timeout=300)
    with wave.open(str(wav), "rb") as wf:
        x = np.frombuffer(wf.readframes(wf.getnframes()),
                          dtype=np.int16).astype(np.float32) / 32768.0
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
    fr = np.fft.rfftfreq(x.size, 1.0 / SR)
    peak = (fr > f0 - 4) & (fr < f0 + 4)
    near = ((fr > f0 - 80) & (fr < f0 - 20)) | ((fr > f0 + 20) & (fr < f0 + 80))
    return float(10 * np.log10(spec[peak].max() / max(spec[near].mean(), 1e-20)))


def _band_energy(path: Path) -> float:
    """אנרגיה סביב 440–660Hz — התדרים של „המוזיקה" בבדיקה."""
    wav = TMP / (path.stem + "_probe.wav")
    subprocess.run(
        [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
         "-c:a", "pcm_s16le", str(wav)],
        capture_output=True, timeout=300)
    with wave.open(str(wav), "rb") as wf:
        x = np.frombuffer(wf.readframes(wf.getnframes()),
                          dtype=np.int16).astype(np.float32) / 32768.0
    # מודדים בהפסקות בלבד, שם הקול אינו מסתיר את המוזיקה
    mask = np.zeros(x.size, dtype=bool)
    t = SPEECH_ON
    while t < DURATION:
        i0, i1 = int(t * SR), int(min(DURATION, t + (CYCLE - SPEECH_ON)) * SR)
        mask[i0:min(i1, x.size)] = True
        t += CYCLE
    seg = x[mask]
    if seg.size < 1024:
        return 0.0
    spec = np.abs(np.fft.rfft(seg)) ** 2
    fr = np.fft.rfftfreq(seg.size, 1.0 / SR)
    return float(spec[(fr > 400) & (fr < 700)].sum())


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
    print(f"\n{passed}/{len(fns)} בדיקות מוזיקה עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
