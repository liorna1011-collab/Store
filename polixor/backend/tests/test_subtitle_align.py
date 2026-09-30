"""
בדיקות לתיקון זמני המילים (services/subtitle_align).

האודיו סינתטי עם זמנים ידועים: "מילים" הן פרצי צליל מעל רעש רקע חלש,
כך שאפשר לבדוק שכל תיקון מקרב את הכתובית לזמן האמיתי – ושהטקסט לא
משתנה לעולם.

הרצה:  python3 tests/test_subtitle_align.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxalign_"))

import numpy as np                                                   # noqa: E402

from polixor.services import subtitle_align as sa                   # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402

RATE = 16000
TMP = Path(tempfile.mkdtemp(prefix="pxalignwav_"))


def _audio(bursts: list[tuple[float, float]], total: float, *, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.normal(0.0, 0.002, int(total * RATE)).astype(np.float32)
    t = np.arange(x.size) / RATE
    for a, b in bursts:
        m = (t >= a) & (t < b)
        x[m] += 0.3 * np.sin(2 * np.pi * 220 * t[m]) * (1 + 0.3 * np.sin(2 * np.pi * 5 * t[m]))
    return x


def _wav(name: str, x: np.ndarray) -> Path:
    p = TMP / name
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return p


def _energy(x: np.ndarray) -> sa.Energy:
    return sa.energy_from_samples(x, 0.0)


def _w(s: float, e: float, text: str = "מילה", p: float = 0.9) -> Word:
    return Word(start=s, end=e, text=text, probability=p)


# --------------------------------------------------------------------------
def test_early_start_snaps_to_speech_onset():
    # דיבור 2.0-2.5; המזהה "מתח" את תחילת המילה לתוך השקט מ-1.2
    x = _audio([(2.0, 2.5)], 4.0)
    times, issues = sa.repair_words([_w(1.2, 2.5)], _energy(x))
    assert abs(times[0][0] - 2.0) <= 0.04, times
    assert [i["kind"] for i in issues] == ["early_start"]


def test_late_end_pulled_back_to_speech_offset():
    x = _audio([(1.0, 1.4)], 4.0)
    times, issues = sa.repair_words([_w(1.0, 2.3)], _energy(x))
    assert abs(times[0][1] - 1.4) <= 0.04, times
    assert "late_end" in [i["kind"] for i in issues]


def test_late_start_moved_back_to_real_onset():
    # הדיבור מתחיל ב-1.0 אחרי שקט; המזהה אומר 1.2
    x = _audio([(1.0, 1.6)], 3.0)
    times, issues = sa.repair_words([_w(1.2, 1.6)], _energy(x))
    assert abs(times[0][0] - 1.0) <= 0.04, times
    assert [i["kind"] for i in issues] == ["late_start"]


def test_continuous_speech_is_not_moved_back_across_previous_word():
    # שתי מילים ברצף דיבור אחד: השנייה לא "נגררת" אחורה לתוך הראשונה
    x = _audio([(1.0, 2.0)], 3.0)
    times, issues = sa.repair_words([_w(1.0, 1.5, "a"), _w(1.5, 2.0, "b")], _energy(x))
    assert times[1][0] >= times[0][1] - 1e-6
    assert abs(times[1][0] - 1.5) <= 0.04
    assert not [i for i in issues if i["i"] == 1]


def test_overlap_is_removed_and_order_kept():
    x = _audio([(1.0, 2.0)], 3.0)
    times, issues = sa.repair_words([_w(1.0, 1.6, "a"), _w(1.4, 2.0, "b")], _energy(x))
    assert times[1][0] >= times[0][1]
    assert "overlap" in [i["kind"] for i in issues]


def test_zero_length_word_gets_minimum_duration():
    times, issues = sa.repair_words([_w(1.0, 1.0, "a"), _w(1.5, 1.8, "b")], None)
    assert times[0][1] - times[0][0] >= sa.MIN_WORD - 1e-6
    assert times[0][1] <= 1.5
    assert "too_short" in [i["kind"] for i in issues]


def test_zero_length_word_squeezed_before_next_borrows_backwards():
    times, _ = sa.repair_words([_w(0.5, 0.8, "a"), _w(1.0, 1.0, "b"), _w(1.0, 1.3, "c")], None)
    s, e = times[1]
    assert e - s >= sa.MIN_WORD - 1e-6 and s >= times[0][1]


def test_overlong_word_without_audio_is_capped():
    times, issues = sa.repair_words([_w(1.0, 4.0, "כן")], None)
    assert times[0][1] - times[0][0] <= 1.0
    assert "too_long" in [i["kind"] for i in issues]


def test_overlong_word_over_silence_is_trimmed_to_speech():
    x = _audio([(3.2, 3.6)], 5.0)
    times, issues = sa.repair_words([_w(1.0, 3.6, "כן")], _energy(x))
    assert abs(times[0][0] - 3.2) <= 0.04 and abs(times[0][1] - 3.6) <= 0.04, times
    assert times[0][1] - times[0][0] <= sa.MAX_WORD


def test_long_real_speech_is_flagged_not_cut():
    x = _audio([(1.0, 3.5)], 5.0)
    times, issues = sa.repair_words([_w(1.0, 3.5, "יאללההה")], _energy(x))
    assert abs(times[0][1] - 3.5) <= 0.04
    assert "long_speech" in [i["kind"] for i in issues]


def test_word_in_silence_moves_to_nearby_speech_or_is_marked():
    x = _audio([(2.0, 2.4)], 4.0)
    times, issues = sa.repair_words([_w(1.7, 1.9, "a")], _energy(x))
    assert abs(times[0][0] - 2.0) <= 0.04, times
    assert [i["kind"] for i in issues] == ["in_silence"]
    far = _audio([(3.5, 3.8)], 4.0)
    times, issues = sa.repair_words([_w(1.0, 1.3, "a")], _energy(far))
    assert times[0] == (1.0, 1.3)
    assert [i["kind"] for i in issues] == ["no_speech"]


def test_correct_timing_is_left_alone():
    x = _audio([(1.0, 1.4), (1.6, 2.1)], 3.0)
    times, issues = sa.repair_words([_w(1.0, 1.4, "a"), _w(1.6, 2.1, "b")], _energy(x))
    assert issues == [], issues
    assert times == [(1.0, 1.4), (1.6, 2.1)]


def test_flat_audio_is_not_trusted():
    x = np.random.default_rng(3).normal(0, 0.1, RATE * 3).astype(np.float32)
    e = _energy(x)
    assert not e.valid
    times, issues = sa.repair_words([_w(1.0, 1.4)], e)
    assert issues == [] and times == [(1.0, 1.4)]


def test_repetition_loop_detection():
    assert sa.repetition_loop("תודה תודה תודה תודה תודה תודה")
    assert sa.repetition_loop("and then and then and then and then")
    assert not sa.repetition_loop("לא לא לא, אני לא מסכים")
    assert not sa.repetition_loop("this is a normal sentence with words")


def test_hallucination_needs_silence_and_a_textual_signal_to_drop():
    silent = _energy(_audio([(5.0, 5.5)], 6.0))
    seg = Segment(start=1.0, end=3.0, text="תודה שצפיתם", words=[_w(1.0, 3.0, "תודה")])
    assert sa.hallucination_action(sa.hallucination_signals(seg, silent)) == "drop"
    # מישהו באמת אמר „תודה שצפיתם" – נשאר
    spoken = _energy(_audio([(1.0, 3.0)], 6.0))
    assert sa.hallucination_action(sa.hallucination_signals(seg, spoken)) is None
    # שקט בלבד, בלי סימן טקסטואלי – רק סימון לבדיקה
    plain = Segment(start=1.0, end=3.0, text="אני הולך הביתה", words=[_w(1.0, 3.0)])
    assert sa.hallucination_action(sa.hallucination_signals(plain, silent)) == "flag"


def _transcript() -> TranscriptResult:
    return TranscriptResult(segments=[
        Segment(start=0.8, end=2.5, text="שלום לכולם",
                words=[_w(0.8, 1.5, "שלום"), _w(1.5, 2.5, "לכולם")]),
        Segment(start=5.0, end=6.5, text="תודה שצפיתם",
                words=[_w(5.0, 5.7, "תודה"), _w(5.7, 6.5, "שצפיתם")],
                no_speech_prob=0.7, avg_logprob=-1.2),
        Segment(start=7.0, end=7.8, text="ביי", words=[_w(7.0, 7.8, "ביי")]),
    ], language="he", duration=9.0)


def test_align_transcript_end_to_end_with_real_wav():
    # "שלום" 1.2-1.6, "לכולם" 1.7-2.2, שקט באזור המשפט השני, "ביי" 7.0-7.4
    x = _audio([(1.2, 1.6), (1.7, 2.2), (7.0, 7.4)], 9.0)
    wav = _wav("clip.wav", x)
    tr = _transcript()
    data = sa.align_transcript(tr, energy_fn=lambda a, b: sa.energy_for(wav, a, b))
    out = sa.apply(tr, data)
    texts = [s.text for s in out.segments]
    assert texts == ["שלום לכולם", "ביי"], texts                 # ההזיה הוסרה
    w = out.segments[0].words
    assert abs(w[0].start - 1.2) <= 0.04 and abs(w[1].end - 2.2) <= 0.04
    assert abs(out.segments[1].words[0].end - 7.4) <= 0.04
    assert data["stats"]["dropped"] == 1
    # הטקסט של כל מילה נשאר זהה
    assert [x.text for x in w] == ["שלום", "לכולם"]
    # המקור לא השתנה
    assert tr.segments[0].words[0].start == 0.8 and len(tr.segments) == 3


def test_align_reuses_previous_and_respects_spans():
    x = _audio([(1.2, 1.6), (1.7, 2.2), (7.0, 7.4)], 9.0)
    wav = _wav("clip2.wav", x)
    calls: list[tuple[float, float]] = []

    def fn(a: float, b: float):
        calls.append((a, b))
        return sa.energy_for(wav, a, b)

    tr = _transcript()
    first = sa.align_transcript(tr, energy_fn=fn, spans=[(0.0, 3.0)])
    assert len(calls) == 1 and list(first["checked"]) == ["0"]
    second = sa.align_transcript(tr, energy_fn=fn, spans=[(0.0, 9.0)], previous=first)
    assert len(calls) == 3                                     # רק המשפטים החדשים
    assert list(second["checked"]) == ["0", "1", "2"]
    # משפט בלי בעיות שהטקסט שלו השתנה (הגהה חדשה) – נבדק שוב
    tr.segments[2].text = "ביי ביי"
    third = sa.align_transcript(tr, energy_fn=fn, spans=[(0.0, 9.0)], previous=second)
    assert len(calls) == 4
    # הטקסט השתנה (הגהה) – התיקון הישן לא מוחל
    tr.segments[0].text = "שלום לכולן"
    out = sa.apply(tr, first)
    assert out.segments[0].words[0].start == 0.8


def test_apply_ignores_other_versions_and_missing_data():
    tr = _transcript()
    assert sa.apply(tr, None) is tr
    assert sa.apply(tr, {"version": 999, "segments": []}) is tr


# --------------------------------------------------------------------------
# יישור כפוי (אופציונלי) – עם מודל מדומה
# --------------------------------------------------------------------------
class _Span:
    def __init__(self, start, end):
        self.start, self.end = start, end


class _FakeBackend:
    """מחזיר לכל מילה טווח פריימים קבוע: 50 פריימים לשנייה."""

    def __init__(self, word_times):
        self.word_times, self.seen = word_times, []

    def romanize(self, w):
        return {"שלום": "shlvm", "לכולם": "lkvlm", "123": "123"}.get(w, w)

    def align(self, samples, words):
        self.seen.append(list(words))
        frames = int(samples.size / RATE * 50)
        return [[_Span(int(a * 50), int(b * 50))] for a, b in self.word_times[:len(words)]], frames


def test_romanize_keeps_only_model_letters():
    from polixor.services import forced_align as fa

    out = fa.romanize_words(["שלום", "123", "Hi!"], lambda w: {"שלום": "shlvm"}.get(w, w))
    assert out == ["shlvm", "", "hi"]


def test_forced_aligner_maps_frames_to_source_time_and_skips_unromanizable():
    from polixor.services import forced_align as fa

    x = _audio([(1.2, 1.6), (1.7, 2.2)], 4.0)
    wav = _wav("fa.wav", x)
    # החלון מתחיל ב-0.55 (0.8 פחות PAD); המודל "מצא" את המילים 0.65-1.05, 1.15-1.65 בחלון
    be = _FakeBackend([(0.65, 1.05), (1.15, 1.65)])
    align = fa.make_aligner(wav, backend=be)
    got = align(0.8, 2.5, ["שלום", "123", "לכולם"])
    assert be.seen == [["shlvm", "lkvlm"]]                      # המספר לא נשלח למודל
    assert got[1] is None
    assert abs(got[0][0] - 1.2) <= 0.03 and abs(got[2][1] - 2.2) <= 0.03, got


def test_forced_alignment_feeds_the_repair_and_is_recorded():
    x = _audio([(1.2, 1.6), (1.7, 2.2)], 4.0)
    wav = _wav("fa2.wav", x)
    tr = TranscriptResult(segments=[Segment(start=0.5, end=2.6, text="שלום לכולם",
                                            words=[_w(0.5, 1.3, "שלום"), _w(1.3, 2.6, "לכולם")])],
                          language="he", duration=4.0)

    def aligner(a, b, words):
        return [(1.21, 1.59), (1.71, 2.19)]
    data = sa.align_transcript(tr, energy_fn=lambda a, b: sa.energy_for(wav, a, b), aligner=aligner)
    kinds = [x["kind"] for x in data["segments"][0]["issues"]]
    assert kinds.count("forced") == 2
    assert data["stats"]["forced_alignment"] is True and data["stats"]["words_retimed"] == 2
    w = sa.apply(tr, data).segments[0].words
    assert abs(w[0].start - 1.2) <= 0.04 and abs(w[1].end - 2.2) <= 0.04
    # יישור שנכשל (None) – חוזרים לתיקון לפי אנרגיה בלבד
    data2 = sa.align_transcript(tr, energy_fn=lambda a, b: sa.energy_for(wav, a, b),
                                aligner=lambda a, b, ws: None)
    assert "forced" not in [x["kind"] for x in data2["segments"][0]["issues"]]


def test_forced_align_status_reports_missing_components():
    from polixor.services import forced_align as fa

    st = fa.status()
    assert set(st) == {"available", "backend", "missing"}
    if not st["available"]:
        assert st["missing"] and fa.make_aligner(TMP / "x.wav") is None


# --------------------------------------------------------------------------
# מדידה וסקריפט ההשוואה
# --------------------------------------------------------------------------
def test_measure_counts_words_in_silence_and_improves_after_repair():
    x = _audio([(1.2, 1.6), (1.7, 2.2), (7.0, 7.4)], 9.0)
    wav = _wav("m.wav", x)
    tr = _transcript()
    fn = lambda a, b: sa.energy_for(wav, a, b)                    # noqa: E731
    before = sa.measure(tr, fn)
    after = sa.measure(sa.apply(tr, sa.align_transcript(tr, energy_fn=fn)), fn)
    assert before["start_in_silence"] >= 1 and before["end_in_silence"] >= 1
    assert after["problem_rate"] < before["problem_rate"]
    assert after["start_in_silence"] == 0 and after["end_in_silence"] == 0


def test_alignment_spike_script_runs_on_files_and_compares_to_truth():
    import importlib.util
    import json as _json

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("spike", root / "scripts" / "alignment_spike.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    x = _audio([(1.2, 1.6), (1.7, 2.2), (7.0, 7.4)], 9.0)
    wav = _wav("s.wav", x)
    tj = TMP / "t.json"
    tj.write_text(_json.dumps({"language": "he", "duration": 9.0, "segments": [
        {"start": s.start, "end": s.end, "text": s.text,
         "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob,
         "words": [w.to_dict() for w in s.words]} for s in _transcript().segments]},
        ensure_ascii=False), "utf-8")
    srt = TMP / "truth.srt"
    srt.write_text("1\n00:00:01,200 --> 00:00:02,200\nשלום לכולם\n\n"
                   "2\n00:00:07,000 --> 00:00:07,400\nביי\n", "utf-8")
    assert mod.parse_srt(srt.read_text("utf-8"))[0] == (1.2, 2.2, "שלום לכולם")
    assert mod.main(["--audio", str(wav), "--transcript", str(tj), "--truth", str(srt),
                     "--out", str(TMP)]) == 0
    rep = _json.loads(sorted(TMP.glob("alignment-spike-*.json"))[-1].read_text("utf-8"))
    raw, fixed = rep["methods"]["raw"], rep["methods"]["energy"]
    assert fixed["truth"]["mean_start_error_ms"] < raw["truth"]["mean_start_error_ms"]
    assert fixed["truth"]["mean_start_error_ms"] <= 40
    assert fixed["problem_rate"] < raw["problem_rate"]


def test_reexport_loads_the_saved_timing_without_recomputing():
    """ייצוא מחדש: התמלול האפקטיבי = מקור + הגהה + זמנים שמורים (בלי אודיו)."""
    import json as _json
    from types import SimpleNamespace

    from polixor import pipeline

    x = _audio([(1.2, 1.6), (1.7, 2.2), (7.0, 7.4)], 9.0)
    wav = _wav("re.wav", x)
    tr = _transcript()
    tj = TMP / "re_transcript.json"
    tj.write_text(_json.dumps({"language": "he", "duration": 9.0, "segments": [
        {"start": s.start, "end": s.end, "text": s.text, "no_speech_prob": s.no_speech_prob,
         "avg_logprob": s.avg_logprob, "words": [w.to_dict() for w in s.words]}
        for s in tr.segments]}, ensure_ascii=False), "utf-8")
    timing = sa.save(TMP / "re_timing.json",
                     sa.align_transcript(tr, energy_fn=lambda a, b: sa.energy_for(wav, a, b)))
    wav.unlink()                                                 # האודיו לא נדרש יותר
    job = SimpleNamespace(artifacts={"transcript_path": str(tj), "timing_path": str(timing)})
    out = pipeline.load_transcript_for_job(job)
    assert [s.text for s in out.segments] == ["שלום לכולם", "ביי"]
    assert abs(out.segments[0].words[0].start - 1.2) <= 0.04
    # פרויקט ישן בלי קובץ זמנים – נטען בדיוק כמו קודם
    old = pipeline.load_transcript_for_job(SimpleNamespace(artifacts={"transcript_path": str(tj)}))
    assert len(old.segments) == 3 and old.segments[0].words[0].start == 0.8


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
    print(f"\n{passed}/{len(fns)} בדיקות זמני כתוביות עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
