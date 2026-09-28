"""
בדיקות למאסטרינג האודיו.

הבדיקות מייצרות קבצי WAV עם מאפיינים **ידועים מראש** — רצפת רעש
בעוצמה מוגדרת, רעידות בתדר מוגדר, עוצמה מוגדרת — ובודקות שהמדידה
מוצאת אותם ושההחלטה מתאימה. כך אפשר לבדוק „לא לעבד מקור טוב"
באמת ולא רק לקוות.

הרצה:  python3 tests/test_mastering.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxmst_"))

from polixor.config import PATHS                                 # noqa: E402

PATHS.ensure()

from polixor.services import audio_mastering as am               # noqa: E402

TMP = Path(os.environ["POLIXOR_DATA_DIR"]) / "audio"
TMP.mkdir(parents=True, exist_ok=True)
SR = 48000


# ==========================================================================
# ייצור קבצי בדיקה עם מאפיינים ידועים
# ==========================================================================
def make_wav(name: str, *, seconds: float = 15.0, speech_db: float = -14.0,
             noise_db: float | None = None, rumble_db: float | None = None,
             gain_db: float = 0.0, pauses: bool = True,
             quiet_half: bool = False, seed: int = 7) -> Path:
    """
    בונה „דיבור" סינתטי: נשא 175Hz מאופנן בקצב הברות, עם הפסקות
    אמיתיות בין משפטים (2 שניות דיבור, 1 שנייה שקט).

    `speech_db` הוא ה-RMS של הדיבור עצמו, `noise_db` רצפת הרעש
    הרציפה, ו-`rumble_db` טון 45Hz רציף. כל אחד מהם מוגדר במפורש,
    ולכן היחסים בין הדיבור לרעש ידועים לנו מראש.
    """
    n = int(SR * seconds)
    t = np.arange(n, dtype=np.float64) / SR
    carrier = np.sin(2 * np.pi * 175.0 * t)
    syllables = 0.5 + 0.5 * np.sin(2 * np.pi * 4.5 * t)
    voice = carrier * syllables
    gate = (np.mod(t, 3.0) < 2.0).astype(np.float64) if pauses \
        else np.ones_like(t)
    voice *= gate

    active = gate > 0.5
    cur = float(np.sqrt(np.mean(voice[active] ** 2)))
    voice *= (10.0 ** (speech_db / 20.0)) / max(cur, 1e-12)

    if quiet_half:      # חצי מהזמן בעוצמה נמוכה בהרבה → LRA רחב
        voice *= np.where(np.mod(t, 9.0) < 4.5, 1.0, 0.09)

    out = voice
    rng = np.random.default_rng(seed)
    if noise_db is not None:
        noise = rng.standard_normal(n)
        # מסננים את התדרים הנמוכים מהרעש כדי לבודד אותו מ„רעידות"
        spec = np.fft.rfft(noise)
        spec[np.fft.rfftfreq(n, 1.0 / SR) < 250.0] = 0.0
        noise = np.fft.irfft(spec, n=n)
        noise *= (10.0 ** (noise_db / 20.0)) / max(
            float(np.sqrt(np.mean(noise ** 2))), 1e-12)
        out = out + noise
    if rumble_db is not None:
        rumble = np.sin(2 * np.pi * 45.0 * t)
        rumble *= (10.0 ** (rumble_db / 20.0)) * math.sqrt(2.0)
        out = out + rumble

    out = out * (10.0 ** (gain_db / 20.0))
    pcm = np.clip(out, -1.0, 1.0)
    data = (pcm * 32767.0).astype(np.int16)

    path = TMP / f"{name}.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(data.tobytes())
    return path


def make_wav_at_lufs(name: str, lufs: float, **kw) -> Path:
    """
    בונה קובץ שעוצמתו המשולבת היא בדיוק `lufs`.

    LUFS אינו RMS: הוא משוקלל-K וממותג, ולכן טון בתדר נמוך נמדד
    נמוך מה-RMS שלו. במקום להניח את ההפרש — מייצרים, מודדים,
    ומתקנים לפי המדידה.
    """
    path = make_wav(name, speech_db=lufs, **kw)
    got = am.measure(path).lufs
    if got is None:
        return path
    return make_wav(name, speech_db=lufs + (lufs - got), **kw)


_CACHE: dict[str, am.AudioMeasurement] = {}


def measured(name: str, **kw) -> am.AudioMeasurement:
    if name not in _CACHE:
        _CACHE[name] = am.measure(make_wav(name, **kw))
    return _CACHE[name]


def measured_at_lufs(name: str, lufs: float, **kw) -> am.AudioMeasurement:
    key = f"{name}@{lufs}"
    if key not in _CACHE:
        _CACHE[key] = am.measure(make_wav_at_lufs(name, lufs, **kw))
    return _CACHE[key]


def clean() -> am.AudioMeasurement:
    """מקור „טוב": בדיוק ‎-14 LUFS, בלי רעש ובלי רעידות."""
    return measured_at_lufs("clean", -14.0)


# ==========================================================================
# מדידה
# ==========================================================================
def test_measurement_reports_real_loudness():
    m = clean()
    assert m.ok, m.error
    assert m.lufs is not None and m.true_peak is not None
    assert -22.0 < m.lufs < -6.0, m.to_dict()
    assert m.duration > 14.0


def test_noise_floor_is_measured_only_in_pauses():
    """
    רגרסיה: אחוזון נמוך של כלל האודיו מודד דיבור חלש, לא רעש.
    על קובץ נקי לגמרי הוא „מצא" רעש ב--29dBFS והפעיל עליו
    הפחתת רעש מיותרת.
    """
    q = clean()
    noisy = measured("noisy", speech_db=-14.0, noise_db=-38.0)
    assert q.noise_floor_db is not None and noisy.noise_floor_db is not None
    # המדידה מבדילה בבירור בין השניים
    assert noisy.noise_floor_db - q.noise_floor_db > 25.0, (
        q.noise_floor_db, noisy.noise_floor_db)
    # והרצפה שנמדדה קרובה לרעש שהוזרק בפועל
    assert abs(noisy.noise_floor_db - (-38.0)) < 6.0, noisy.noise_floor_db


def test_no_pauses_means_the_floor_is_unknown_not_guessed():
    m = measured("nopause", speech_db=-14.0, pauses=False)
    assert m.noise_floor_db is None, m.to_dict()
    assert m.snr_db is None
    plan = am.plan_mastering(m)
    step = [s for s in plan.steps if s.action == "denoise"][0]
    assert step.applied is False
    assert "הפסקות" in step.reason, step.reason


def test_digital_silence_is_not_reported_below_16_bit():
    m = clean()
    assert m.noise_floor_db is not None
    assert m.noise_floor_db >= -96.5, m.noise_floor_db


def test_clipping_is_detected_as_runs_not_single_samples():
    m = measured("clipped", speech_db=-14.0, gain_db=18.0)
    assert m.is_clipped, m.to_dict()
    assert m.clipped_samples > 1000
    assert not clean().is_clipped


def test_low_band_energy_finds_the_rumble():
    r = measured("rumble", speech_db=-14.0, rumble_db=-20.0)
    assert r.low_band_ratio is not None
    assert r.low_band_ratio > am.RUMBLE_RATIO, r.low_band_ratio
    assert (clean().low_band_ratio or 0.0) < 0.01


def test_missing_file_is_reported_not_faked():
    m = am.measure(TMP / "does_not_exist.wav")
    assert m.ok is False and m.error
    assert m.lufs is None


def test_measurement_is_json_serialisable():
    json.dumps(clean().to_dict(), ensure_ascii=False)


# ==========================================================================
# החלטות — העיקרון: לא לעבד מקור טוב
# ==========================================================================
def test_good_source_is_left_alone():
    """
    הדרישה: „לא לבצע עיבוד הרסני כאשר המקור כבר טוב".
    מקור ב--14 LUFS, בלי רעש ובלי רעידות, יוצא בלי שום עיבוד.
    """
    plan = am.plan_mastering(clean(), target="social")
    assert plan.is_noop, plan.filter_chain()
    assert plan.filter_chain() == ""
    assert any("כבר עומד ביעד" in n for n in plan.notes), plan.notes


def test_every_step_appears_with_a_reason_even_when_skipped():
    plan = am.plan_mastering(clean())
    actions = [s.action for s in plan.steps]
    for expected in ("highpass", "denoise", "gate", "level", "compress",
                     "normalize", "limit"):
        assert expected in actions, actions
    for s in plan.steps:
        assert s.reason, s.to_dict()
        if not s.applied:
            assert s.filter == "", s.to_dict()


def test_quiet_source_is_only_normalised():
    m = measured("quiet", speech_db=-32.0)
    plan = am.plan_mastering(m, target="social")
    applied = [s.action for s in plan.applied_steps]
    assert applied == ["normalize"], applied
    assert "loudnorm" in plan.filter_chain()
    assert "measured_I" in plan.filter_chain(), "נרמול חייב להיות דו-מעברי"
    assert "linear=true" in plan.filter_chain()


def test_loudness_already_on_target_is_not_touched():
    plan = am.plan_mastering(clean(), target="social")
    step = [s for s in plan.steps if s.action == "normalize"][0]
    assert step.applied is False
    assert "לא נוגעים" in step.reason


def test_noisy_source_gets_denoise_sized_to_the_noise():
    m = measured("noisy", speech_db=-14.0, noise_db=-38.0)
    plan = am.plan_mastering(m)
    step = [s for s in plan.steps if s.action == "denoise"][0]
    assert step.applied, step.reason
    assert step.params["reduction_db"] <= am.MAX_NOISE_REDUCTION
    assert "afftdn" in step.filter


def test_denoise_is_sized_after_the_highpass_not_before():
    """
    רגרסיה: הרעידות בתדר נמוך ניפחו את רצפת הרעש הנמדדת, והמנוע
    הזמין הפחתת רעש של 12dB על קול שהסינון שלפניה כבר ניקה.
    """
    m = measured("rumble", speech_db=-14.0, rumble_db=-20.0)
    plan = am.plan_mastering(m)
    hp = [s for s in plan.steps if s.action == "highpass"][0]
    dn = [s for s in plan.steps if s.action == "denoise"][0]
    assert hp.applied, hp.reason
    assert dn.params.get("after_highpass") is True, dn.to_dict()
    # אחרי הסינון אין רעש רחב-סרט → אין הפחתת רעש
    assert dn.applied is False, dn.reason


def test_wide_dynamics_get_levelled():
    m = measured("wide", speech_db=-14.0, quiet_half=True)
    plan = am.plan_mastering(m)
    step = [s for s in plan.steps if s.action == "level"][0]
    assert step.applied, (step.reason, m.lra)
    assert "dynaudnorm" in step.filter


def test_even_dynamics_are_not_levelled():
    plan = am.plan_mastering(clean())
    step = [s for s in plan.steps if s.action == "level"][0]
    assert step.applied is False
    assert "אחידה" in step.reason


def test_clipped_source_is_reported_not_silently_repaired():
    """
    הדרישה שלא להסתיר מגבלה: עיוות שנוצר בהקלטה אינו ניתן לביטול,
    והמערכת אומרת את זה במקום להציג „תוקן".
    """
    m = measured("clipped", speech_db=-14.0, gain_db=18.0)
    plan = am.plan_mastering(m)
    assert any("כבר חתוך" in w for w in plan.warnings), plan.warnings
    assert not any("תוקן" in w or "שוחזר" in w for w in plan.warnings)


def test_loud_peaks_get_gain_plus_limiter_not_a_silent_fallback():
    """
    כשההגבר הדרוש גדול מהמרווח עד תקרת השיא, `loudnorm` עם
    `linear=true` נופל בשקט למצב דינמי ומחטיא את היעד. כאן
    ההחלטה מפורשת: הגבר מלא + לימיטר שתופס את הפסגות.
    """
    m = am.AudioMeasurement(
        ok=True, lufs=-22.0, true_peak=-6.0, lra=3.0, threshold=-32.0,
        peak_db=-6.0, rms_db=-20.0, speech_level_db=-10.0,
        noise_floor_db=-80.0, noise_floor_hp_db=-80.0, silence_seconds=2.0,
        crest_db=14.0, silence_ratio=0.3, low_band_ratio=0.0, duration=20.0)
    plan = am.plan_mastering(m, target="social")
    norm = [s for s in plan.steps if s.action == "normalize"][0]
    lim = [s for s in plan.steps if s.action == "limit"][0]
    assert norm.applied and norm.params["mode"] == "gain_limited", norm.to_dict()
    assert "volume=" in norm.filter, norm.filter
    assert lim.applied and "alimiter" in lim.filter, lim.to_dict()
    assert "לימיטר" in norm.reason


def test_enough_headroom_uses_pure_linear_gain():
    m = am.AudioMeasurement(
        ok=True, lufs=-22.0, true_peak=-16.0, lra=3.0, threshold=-32.0,
        peak_db=-16.0, rms_db=-20.0, speech_level_db=-18.0,
        noise_floor_db=-80.0, noise_floor_hp_db=-80.0, silence_seconds=2.0,
        crest_db=4.0, silence_ratio=0.3, low_band_ratio=0.0, duration=20.0)
    plan = am.plan_mastering(m, target="social")
    norm = [s for s in plan.steps if s.action == "normalize"][0]
    assert norm.params["mode"] == "linear", norm.to_dict()
    assert "loudnorm" in norm.filter and "linear=true" in norm.filter


def test_limiter_does_not_double_up_with_normalisation():
    m = measured("quiet", speech_db=-32.0)
    plan = am.plan_mastering(m)
    lim = [s for s in plan.steps if s.action == "limit"][0]
    assert lim.applied is False
    assert "פעמיים" in lim.reason


def test_switches_can_disable_processing():
    m = measured("noisy", speech_db=-14.0, noise_db=-38.0)
    plan = am.plan_mastering(m, allow_denoise=False, allow_compress=False)
    for action in ("denoise", "compress"):
        step = [s for s in plan.steps if s.action == action][0]
        assert step.applied is False
        assert "כובת" in step.reason


def test_targets_differ_and_are_respected():
    m = measured("quiet", speech_db=-32.0)
    social = am.plan_mastering(m, target="social")
    broadcast = am.plan_mastering(m, target="broadcast")
    assert "I=-14.0" in social.filter_chain()
    assert "I=-23.0" in broadcast.filter_chain()
    assert am.get_target("nonsense").name == am.DEFAULT_TARGET


def test_unmeasurable_audio_produces_no_processing():
    m = am.AudioMeasurement(ok=False, error="אין פס קול")
    plan = am.plan_mastering(m)
    assert plan.is_noop
    assert plan.warnings


def test_plan_is_json_serialisable():
    blob = json.dumps(am.plan_mastering(clean()).to_dict(),
                      ensure_ascii=False)
    assert "steps" in blob


# ==========================================================================
# ביצוע ואימות — מדידה לפני ואחרי
# ==========================================================================
def test_processing_reaches_the_target_and_verifies():
    """
    הדרישה: „לפני ואחרי העיבוד למדוד Loudness".
    התוצאה נמדדת שוב, ורק אם היא עומדת ביעד היא מסומנת כמאומתת.
    """
    src = make_wav("quiet_run", speech_db=-32.0)
    res = am.master_file(src, TMP / "quiet_out.wav", target="social")
    assert res.processed, res.issues
    assert res.after is not None and res.after.ok
    assert res.plan.before.lufs is not None
    assert abs(res.after.lufs - (-14.0)) <= am.VERIFY_LUFS_TOLERANCE, (
        res.plan.before.lufs, res.after.lufs)
    assert res.verified is True and res.needs_review is False, res.issues
    assert "LUFS" in res.summary()


def test_true_peak_stays_under_the_ceiling():
    src = make_wav("quiet_run2", speech_db=-30.0)
    res = am.master_file(src, TMP / "quiet_out2.wav", target="social")
    assert res.processed
    assert res.after.true_peak is not None
    assert res.after.true_peak <= -1.0 + am.VERIFY_TP_TOLERANCE, \
        res.after.true_peak


def test_processing_does_not_add_clipping():
    src = make_wav("quiet_run3", speech_db=-28.0)
    res = am.master_file(src, TMP / "quiet_out3.wav", target="social")
    assert res.processed
    assert res.after.clip_ratio <= max(res.plan.before.clip_ratio * 1.5,
                                       am.CLIP_RATIO_BAD)


def test_good_source_is_not_rewritten_at_all():
    src = make_wav_at_lufs("good_run", -14.0)
    dst = TMP / "good_out.wav"
    if dst.exists():
        dst.unlink()
    res = am.master_file(src, dst, target="social")
    assert res.processed is False
    assert not dst.exists(), "מקור טוב לא אמור לייצר קובץ מעובד"
    assert "לא בוצע עיבוד" in res.summary()


def test_failed_verification_marks_needs_review_not_completed():
    """
    הדרישה מ-§25: כשיש בעיה — „לא לסמן Completed. סמן needs_review".
    כאן מזייפים מדידת „אחרי" שלא הגיעה ליעד ובודקים את הסימון.
    """
    before = clean()
    plan = am.plan_mastering(
        am.AudioMeasurement(ok=True, lufs=-30.0, true_peak=-12.0, lra=3.0,
                            threshold=-40.0, peak_db=-12.0, rms_db=-30.0,
                            speech_level_db=-12.0, noise_floor_db=-70.0,
                            noise_floor_hp_db=-70.0, silence_seconds=2.0,
                            crest_db=8.0, silence_ratio=0.3,
                            low_band_ratio=0.0, duration=15.0),
        target="social")
    result = am.MasteringResult(plan=plan, processed=True)
    result.after = am.AudioMeasurement(ok=True, lufs=-20.0, true_peak=-3.0,
                                       peak_db=-3.0, duration=15.0)
    am._verify(result)
    assert result.needs_review is True
    assert result.verified is False
    assert any("היעד" in i for i in result.issues), result.issues
    assert before.ok


def test_silent_result_is_caught():
    plan = am.plan_mastering(clean())
    result = am.MasteringResult(plan=plan, processed=True)
    result.after = am.AudioMeasurement(ok=True, lufs=-70.0, peak_db=-90.0,
                                       duration=15.0)
    am._verify(result)
    assert result.needs_review is True
    assert any("שקטה" in i for i in result.issues), result.issues


def test_levelling_plus_normalising_does_not_overshoot():
    """
    רגרסיה מהפייפליין: על מקור שקט עם טווח דינמי רחב, האיזון
    והדחיסה מרימים את העוצמה בעצמם. הגבר שחושב מהמדידה המקורית
    הביא את הקליפ ל--9.4LUFS במקום ל--14. עכשיו מודדים שוב
    באמצע ורק אז קובעים את ההגבר.
    """
    src = make_wav("overshoot", speech_db=-30.0, quiet_half=True,
                   noise_db=-46.0)
    before = am.measure(src)
    plan = am.plan_mastering(before, target="social")
    applied = {s.action for s in plan.applied_steps}
    assert applied & {"level", "compress"}, applied
    assert "normalize" in applied, applied

    res = am.master(src, TMP / "overshoot_out.wav", plan)
    assert res.processed, res.issues
    assert res.mid is not None, "המדידה האמצעית לא בוצעה"
    assert res.after is not None and res.after.lufs is not None
    assert abs(res.after.lufs - (-14.0)) <= am.VERIFY_LUFS_TOLERANCE, (
        f"לפני {before.lufs}, אמצע {res.mid.lufs}, אחרי {res.after.lufs}")
    assert res.verified and not res.needs_review, res.issues


def test_true_peak_respects_intersample_headroom():
    src = make_wav("peaky", speech_db=-26.0, quiet_half=True)
    res = am.master_file(src, TMP / "peaky_out.wav", target="social")
    if res.processed:
        assert res.after.true_peak is not None
        assert res.after.true_peak <= -1.0 + am.VERIFY_TP_TOLERANCE, \
            res.after.true_peak


def test_result_is_json_serialisable():
    src = make_wav("ser_run", speech_db=-30.0)
    res = am.master_file(src, TMP / "ser_out.wav")
    blob = json.dumps(res.to_dict(), ensure_ascii=False)
    assert "needs_review" in blob and "summary" in blob


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
    print(f"\n{passed}/{len(fns)} בדיקות מאסטרינג עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
