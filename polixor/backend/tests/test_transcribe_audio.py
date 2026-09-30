"""
בדיקות לנתיב האודיו של התמלול ולתאימות PyAV.

faster-whisper 1.2.1 קורא ל-av.open(..., metadata_errors="ignore") ו-PyAV 19
הסיר את הפרמטר. הבדיקות מוודאות ש:
  * requirements.txt נועל את הצירוף שנבדק (faster-whisper 1.2.1 + av 18.1.0);
  * קריאת ה-WAV הישירה זהה לפענוח של faster-whisper עצמו;
  * כש-av.open דוחה את metadata_errors (כמו ב-PyAV 19), הפענוח של
    faster-whisper נופל – והנתיב של Polixor ממשיך לעבוד.

הרצה:  python3 tests/test_transcribe_audio.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxasr_"))

import numpy as np                                               # noqa: E402

from polixor.services import transcribe                          # noqa: E402
from polixor.util.ffmpeg import run_ffmpeg                       # noqa: E402
from polixor.util.wav import read_wav_float32, wav_duration      # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="pxwav_"))


def _tone(name: str, seconds: float = 3.0, rate: int = 16000, channels: int = 1) -> Path:
    out = TMP / name
    run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                "-ac", str(channels), "-ar", str(rate), "-acodec", "pcm_s16le", str(out)])
    return out


def test_requirements_pin_the_verified_pair():
    req = (ROOT / "requirements.txt").read_text("utf-8")
    assert "faster-whisper==1.2.1" in req
    assert "av==18.1.0" in req
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert '"faster-whisper==1.2.1"' in pyproject and '"av==18.1.0"' in pyproject


def test_direct_wav_matches_faster_whisper_decoder():
    from faster_whisper.audio import decode_audio

    wav = _tone("a.wav")
    ours = read_wav_float32(wav)
    theirs = decode_audio(str(wav))
    assert ours is not None and ours.dtype == np.float32
    assert ours.shape == theirs.shape
    assert float(np.abs(ours - theirs).max()) == 0.0
    assert abs(wav_duration(wav) - 3.0) < 0.01


def test_window_read():
    wav = _tone("b.wav", seconds=5.0)
    part = read_wav_float32(wav, start=1.0, duration=2.0)
    assert part is not None and part.shape == (32000,)
    whole = read_wav_float32(wav)
    assert np.array_equal(part, whole[16000:48000])
    tail = read_wav_float32(wav, start=4.5, duration=10.0)
    assert tail.shape == (8000,)


def test_unexpected_formats_fall_back_to_path():
    stereo = _tone("s.wav", channels=2)
    other_rate = _tone("r.wav", rate=44100)
    assert read_wav_float32(stereo) is None
    assert read_wav_float32(other_rate) is None
    assert transcribe.whisper_audio_input(stereo) == str(stereo)
    not_wav = TMP / "x.wav"
    not_wav.write_bytes(b"not a wav file")
    assert read_wav_float32(not_wav) is None


def test_pyav_19_api_change_is_bypassed():
    """מדמה את שינוי ה-API של PyAV 19: av.open דוחה metadata_errors."""
    import av
    from faster_whisper.audio import decode_audio

    wav = _tone("c.wav")
    real_open = av.open

    def open_without_metadata_errors(*args, **kwargs):
        if "metadata_errors" in kwargs:
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        return real_open(*args, **kwargs)

    av.open = open_without_metadata_errors
    try:
        try:
            decode_audio(str(wav))
        except TypeError as exc:
            assert "metadata_errors" in str(exc)
        else:
            raise AssertionError("the simulated PyAV 19 did not break faster-whisper's decoder")
        audio = transcribe.whisper_audio_input(wav)
        assert isinstance(audio, np.ndarray) and audio.shape == (48000,)
    finally:
        av.open = real_open


def test_pyav_status_reports_installed_pair():
    st = transcribe.pyav_status()
    assert st["faster_whisper"] and st["av"]
    assert st["compatible"] is True


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
    print(f"\n{passed}/{len(fns)} בדיקות אודיו לתמלול עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
