"""
בדיקות לשיפורי הביצועים (שלב 3): כל שיפור חייב לתת את אותה תוצאה (או
טובה יותר) – רק מהר יותר.

  * היסטוגרמה מהירה – זהה ביט-לביט ל-np.histogram;
  * קטעי שקט מתוך קריאת האודיו – כמו silencedetect של ffmpeg (±10ms);
  * פענוח משותף לניתוח החזותי ולפריסות – פענוח אחד, ניתוב נכון לכל צד;
  * זיהוי פנים – מעבר הפרופיל רק כשאין פנים חזיתיות;
  * תהליכוני המזהה – לא פחות מברירת המחדל, וניתן לקביעה ידנית.

הרצה:  python3 tests/test_performance.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxperf_"))

import numpy as np                                                   # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="pxperfwav_"))
RATE = 16000


def _wav(name: str, x: np.ndarray) -> Path:
    p = TMP / name
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return p


def test_fast_histogram_is_identical():
    from polixor.services.visual import _hist

    rng = np.random.default_rng(7)
    for shape in ((180, 320), (1, 1), (7, 3)):
        g = rng.integers(0, 256, shape, dtype=np.uint8)
        h, _ = np.histogram(g, bins=64, range=(0, 256))
        assert np.array_equal(_hist(g), (h / h.sum()).astype(np.float32))
    for v in (0, 3, 4, 252, 255):
        g = np.full((5, 5), v, np.uint8)
        h, _ = np.histogram(g, bins=64, range=(0, 256))
        assert np.array_equal(_hist(g), (h / h.sum()).astype(np.float32))


def test_silences_from_the_audio_pass_match_ffmpeg():
    from polixor.services.audio import analyze_audio
    from polixor.util.ffmpeg import silence_intervals

    t = np.arange(int(12 * RATE)) / RATE
    x = 0.3 * np.sin(2 * np.pi * 300 * t)
    for a, b in ((2.0, 3.1), (5.0, 5.4), (7.25, 9.0)):          # 5.0-5.4 קצר מדי
        x[(t >= a) & (t < b)] = 0.0005 * np.sin(2 * np.pi * 50 * t[(t >= a) & (t < b)])
    wav = _wav("sil.wav", x)
    mine = analyze_audio(wav).silences
    ref = silence_intervals(wav)
    assert len(mine) == len(ref) == 2, (mine, ref)
    for (a, b), (c, d) in zip(mine, ref):
        assert abs(a - c) <= 0.011 and abs(b - d) <= 0.011, (mine, ref)


def test_silence_runs_edges():
    from polixor.services.audio import silence_runs

    assert silence_runs(np.zeros(0, np.float32), 0.01) == []
    quiet = np.full(100, 1e-4, np.float32)                       # 1 שנייה שקט
    assert silence_runs(quiet, 0.01) == [(0.0, 1.0)]
    loud = np.full(100, 0.5, np.float32)
    assert silence_runs(loud, 0.01) == []
    mixed = np.concatenate([loud, quiet[:50], loud, quiet[:70]])  # 0.5s (קצר), 0.7s בסוף
    assert silence_runs(mixed, 0.01) == [(2.5, 3.2)]


def test_shared_scan_decodes_once_and_routes_frames():
    from polixor.services import frame_scan

    calls = []

    def fake_decode(path, *, width, height, interval, cancel_event=None):
        calls.append((width, height, interval))
        n = int(20 / interval)
        for k in range(n):
            f = np.full((height, width, 3), (k * 7) % 255, np.uint8)
            yield k * interval, f

    feats, tl = frame_scan.scan_visual_and_layouts(
        TMP / "none.mp4", duration=20.0, src_w=1280, src_h=720, sample_fps=1.0,
        detect_faces=False, decode=fake_decode)
    assert len(calls) == 1                                        # פענוח אחד בלבד
    w, h, interval = calls[0]
    assert (w, h) == (960, 540) and abs(interval - 1.0) < 1e-9
    assert feats.n == 20 and feats.width == 320 and feats.height == 180
    assert list(feats.times[:3]) == [0.0, 1.0, 2.0]
    assert tl.samples == 10                                       # כל 2 שניות


def test_shared_scan_uses_the_faster_rate_when_visual_is_sparse():
    from polixor.services import frame_scan

    got = {}

    def fake_decode(path, *, width, height, interval, cancel_event=None):
        got["interval"] = interval
        for k in range(int(20 / interval)):
            yield k * interval, np.zeros((height, width, 3), np.uint8)

    feats, tl = frame_scan.scan_visual_and_layouts(
        TMP / "none.mp4", duration=20.0, src_w=1280, src_h=720, sample_fps=0.25,
        detect_faces=False, decode=fake_decode)
    assert abs(got["interval"] - 2.0) < 1e-9                      # קצב הפריסות (0.5fps)
    assert feats.n == 5 and tl.samples == 10


def test_can_share_only_below_the_keyframe_threshold():
    from polixor.services import frame_scan
    from polixor.services.layout_detect import detectors_available

    if detectors_available():
        assert frame_scan.can_share(30 * 60)
    assert not frame_scan.can_share(0)
    assert not frame_scan.can_share(frame_scan.KEYFRAME_LAYOUT_ABOVE + 1)


class _Cascade:
    def __init__(self, hits):
        self.hits, self.calls = hits, 0

    def detectMultiScale(self, img, **kw):
        self.calls += 1
        return list(self.hits)


def test_profile_pass_runs_only_without_a_frontal_face():
    from polixor.services import layout_detect as ld

    real = dict(ld._CASCADES)
    try:
        frontal = _Cascade([(100, 100, 60, 60)])
        profile = _Cascade([(300, 100, 60, 60)])
        ld._CASCADES.update({"haarcascade_frontalface_default.xml": frontal,
                             "haarcascade_frontalface_alt2.xml": _Cascade([]),
                             "haarcascade_profileface.xml": profile})
        gray = np.zeros((540, 960), np.uint8)
        faces = ld.detect_faces(gray)
        assert len(faces) == 1 and profile.calls == 0
        # בלי פנים חזיתיות – שני מעברי פרופיל (רגיל והפוך), כמו קודם
        frontal.hits = []
        faces = ld.detect_faces(gray)
        assert profile.calls == 2 and len(faces) >= 1
    finally:
        ld._CASCADES.clear()
        ld._CASCADES.update(real)


def test_asr_threads_never_below_default_and_overridable():
    from polixor.services import transcribe

    assert transcribe.cpu_threads() >= 4
    os.environ["POLIXOR_ASR_THREADS"] = "6"
    try:
        assert transcribe.cpu_threads() == 6
    finally:
        del os.environ["POLIXOR_ASR_THREADS"]


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
    print(f"\n{passed}/{len(fns)} בדיקות ביצועים עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
