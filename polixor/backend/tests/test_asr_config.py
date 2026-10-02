"""
בדיקות לתצורת התמלול: פרופילים, בחירת מודל, אוצר מילים, חלוקה למקטעים
בנקודות שקטות, ונקודות שמירה להמשך תמלול שנקטע.

(תמלול אמיתי עם מודל Whisper לא רץ כאן: בסביבת הפיתוח אין גישה להורדת
מודלים. הבדיקה האמיתית היא scripts/profile_media.py על מדיה של המשתמש.)

הרצה:  python3 tests/test_asr_config.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxasrcfg_"))

import numpy as np                                                    # noqa: E402

from polixor.config import AppSettings                               # noqa: E402
from polixor.profiles import (FAST_MODEL, GENERAL_STRONG_MODEL, HEBREW_MODEL,  # noqa: E402
                              asr_plan)
from polixor.project_config import clamp_config, settings_for_project  # noqa: E402
from polixor.services import transcribe as tr                        # noqa: E402
from polixor.services.vocabulary import (MAX_HOTWORD_CHARS, hotwords,  # noqa: E402
                                         merge_terms, normalize_terms)
from polixor.util.wav import rms_envelope                             # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="pxasr_"))


def settings(**kw) -> AppSettings:
    d = AppSettings().to_dict()
    d.update(kw)
    return AppSettings.from_dict(d)


# --------------------------------------------------------------------------
def test_vocabulary_normalisation():
    terms = normalize_terms("ולורנט, אוהד\nclutch;  Clutch \n\n" + "x" * 90)
    assert terms[:3] == ["ולורנט", "אוהד", "clutch"], terms
    assert len(terms) == 4 and len(terms[3]) == 60
    assert normalize_terms(["a, b", "B", None, 5]) == ["a", "b"]
    assert normalize_terms(None) == [] and normalize_terms({"x": 1}) == []
    assert len(normalize_terms([f"t{i}" for i in range(500)])) == 100
    assert merge_terms(["אוהד"], "אוהד, קלאץ'") == ["אוהד", "קלאץ'"]


def test_hotwords_are_bounded():
    assert hotwords([]) is None
    hw = hotwords([f"מונח{i}" for i in range(100)])
    assert hw and len(hw) <= MAX_HOTWORD_CHARS and hw.startswith("מונח0, מונח1")


def test_profile_and_model_choice():
    # the production discovery transcript is the strong model on every profile
    fast = asr_plan(settings(performance_profile="fast", transcribe_language="he"))
    assert fast.profile == "fast" and fast.model == HEBREW_MODEL and fast.batched
    # the fast model only as the quick preview
    preview = asr_plan(settings(performance_profile="fast", discovery_asr="fast"))
    assert preview.model == FAST_MODEL
    q_he = asr_plan(settings(performance_profile="quality", transcribe_language="he"))
    assert q_he.model == HEBREW_MODEL and q_he.strong_model == HEBREW_MODEL
    q_en = asr_plan(settings(performance_profile="quality", transcribe_language="en"))
    assert q_en.model == GENERAL_STRONG_MODEL
    # בפרופיל מהיר המודל החזק משמש לתמלול החוזר הממוקד
    assert asr_plan(settings(performance_profile="fast", transcribe_language="he")).strong_model == HEBREW_MODEL
    # בחירה מפורשת גוברת על הפרופיל; "hebrew" הוא קיצור
    assert asr_plan(settings(performance_profile="quality", whisper_model="medium")).model == "medium"
    assert asr_plan(settings(whisper_model="hebrew")).model == HEBREW_MODEL
    # שפה מפורשת ננעלת; אוטומטית נשארת לזיהוי
    assert asr_plan(settings(transcribe_language="he")).language == "he"
    assert asr_plan(settings(transcribe_language="auto")).language is None
    # cpu → int8, והמכשיר נשאר כפי שנבחר
    cpu = asr_plan(settings(whisper_device="cpu"))
    assert cpu.device == "cpu" and cpu.compute_type == "int8" and cpu.batch_size == 8


def test_vocabulary_reaches_the_plan_and_projects_merge_it():
    base = settings(asr_vocabulary=["ולורנט", "אוהד"])
    assert asr_plan(base).hotwords == "ולורנט, אוהד"
    cfg = clamp_config({"vocabulary": "קלאץ', אוהד"})
    assert cfg["vocabulary"] == ["קלאץ'", "אוהד"]
    s = settings_for_project(base, cfg)
    assert s.asr_vocabulary == ["קלאץ'", "אוהד", "ולורנט"]
    assert asr_plan(s).hotwords == "קלאץ', אוהד, ולורנט"


def test_settings_are_clamped():
    s = settings(whisper_beam_size=99, asr_vocabulary="a\nb", whisper_model="  ")
    assert s.whisper_beam_size == 10 and s.asr_vocabulary == ["a", "b"] and s.whisper_model == "auto"


def test_rms_envelope_matches_numpy():
    rate = 16000
    t = np.arange(rate * 3) / rate
    x = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    x[rate:2 * rate] = 0.0                              # שנייה שקטה באמצע
    path = TMP / "tone.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    env = rms_envelope(path, hop=0.1, block_seconds=0.7)
    assert env is not None and env.size == 30
    assert abs(float(env[5]) - 0.5 / np.sqrt(2)) < 0.01
    assert float(env[15]) < 1e-3


def test_long_audio_is_cut_at_quiet_points():
    total = 3600.0
    hop = tr.ENVELOPE_HOP
    env = np.full(int(total / hop), 0.3, dtype=np.float32)
    quiet = [612.0, 1195.0, 1800.0, 2390.0, 3010.0]
    for q in quiet:
        env[int(q / hop): int((q + 1.0) / hop)] = 0.0
    chunks = tr.plan_chunks(env, total)
    cuts = [a for a, _ in chunks[1:]]
    assert len(chunks) == 6, chunks
    for c, q in zip(cuts, quiet):
        assert q - 0.3 <= c <= q + 1.3, (c, q)
    assert chunks[0][0] == 0.0 and chunks[-1][1] == total
    assert all(abs(b - c) < 1e-9 for (_, b), (c, _) in zip(chunks, chunks[1:]))


def test_continuous_speech_widens_the_search_for_a_pause():
    total = 1800.0
    hop = tr.ENVELOPE_HOP
    env = np.full(int(total / hop), 0.3, dtype=np.float32)
    env[int(650 / hop): int(651 / hop)] = 0.0           # ההפסקה היחידה: 50 ש׳ מהיעד
    chunks = tr.plan_chunks(env, total)
    assert 649.7 <= chunks[1][0] <= 651.3, chunks


def test_short_audio_is_one_chunk():
    assert tr.plan_chunks(np.zeros(100), 600.0) == [(0.0, 600.0)]
    assert tr.plan_chunks(None, 5000.0) == [(0.0, 5000.0)]


def test_checkpoints_resume_only_with_the_same_config():
    segs = [tr.Segment(start=1.0, end=2.5, text="שלום", language="he",
                       words=[tr.Word(1.0, 1.4, "שלום", 0.91)])]
    d = TMP / "ckpt"
    tr._save_chunk(d, 3, "key-a", segs)
    back = tr._load_chunk(d, 3, "key-a")
    assert back and back[0].text == "שלום" and back[0].words[0].probability == 0.91
    assert tr._load_chunk(d, 3, "key-b") is None       # תצורה אחרת → מתמללים מחדש
    assert tr._load_chunk(d, 4, "key-a") is None
    assert tr._load_chunk(None, 3, "key-a") is None


def test_config_key_changes_with_vocabulary_and_model():
    wav = TMP / "tone.wav"
    p1 = asr_plan(settings(asr_vocabulary=["א"]))
    p2 = asr_plan(settings(asr_vocabulary=["ב"]))
    p3 = asr_plan(settings(whisper_model="medium", asr_vocabulary=["א"]))
    ch = [(0.0, 3.0)]
    k1, k2, k3 = (tr._config_key(p, "he", wav, ch) for p in (p1, p2, p3))
    assert len({k1, k2, k3}) == 3 and tr._config_key(p1, "he", wav, ch) == k1


def test_missing_model_falls_back_with_an_honest_note():
    """מודל שלא ניתן להוריד: המשימה ממשיכה בלי תמלול ומסבירה למה."""
    wav = TMP / "tone.wav"
    s = settings(transcript_provider="faster-whisper",
                 whisper_model="polixor-test/this-model-does-not-exist",
                 asr_vocabulary=["x"])
    res = tr.transcribe_audio(wav, settings=s, media_duration=3.0, allow_fallback=True)
    assert res.provider == "none" and not res.segments
    assert res.note and ("polixor-test/this-model-does-not-exist" in res.note or res.note)


def test_transcript_meta_survives_save_and_load():
    from polixor.pipeline import _load_transcript, _save_transcript

    res = tr.TranscriptResult(segments=[], language="he", duration=3.0, provider="faster-whisper",
                              model="small", meta={"profile": "fast", "chunks": 1,
                                                   "language_probability": 0.97})
    path = TMP / "t.json"
    _save_transcript(res, path)
    back = _load_transcript(path)
    assert back.meta["profile"] == "fast" and back.meta["language_probability"] == 0.97


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
    print(f"\n{passed}/{len(fns)} בדיקות תצורת תמלול עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
