"""
ניתוח אודיו אמיתי (DSP) על גבי רשת זמן אחידה.

מחושב בזרימה (streaming) בבלוקים של כמה שניות, כך שגם שידור של 5 שעות
לא נטען לזיכרון בבת אחת. הזיכרון הנדרש תלוי בגודל הבלוק בלבד.

האותות שמחולצים:
  rms_db        – עוצמה לכל פריים (dBFS)
  energy        – עוצמה מנורמלת 0..1 מול רעש הרקע של השידור
  flux          – Spectral Flux: שינוי פתאומי בתוכן התדרים (התרגשות, אירוע)
  centroid      – מרכז כובד ספקטרלי (צווחה/צחוק נוטים לערכים גבוהים)
  zcr           – Zero Crossing Rate
  jump          – קפיצת עוצמה מקומית מול חלון רקע (הסיגנל החזק לצעקות)
  silence       – מסכת שקט
  laughter      – אינדיקציה היוריסטית לצחוק/המולה (אנרגיה + מודולציה)
"""

from __future__ import annotations

import logging
import math
import threading
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ..errors import JobCancelledError, NoAudioError, PolixorError

log = logging.getLogger("polixor.audio")

ProgressFn = Optional[Callable[[float], None]]


@dataclass
class AudioFeatures:
    sample_rate: int = 16000
    hop: float = 0.1                       # שניות בין פריימים
    duration: float = 0.0
    times: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    rms_db: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    energy: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    flux: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    centroid: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    zcr: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    jump: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    silence: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    laughter: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    noise_floor_db: float = -60.0
    peak_db: float = 0.0
    speech_ratio: float = 0.0

    @property
    def n(self) -> int:
        return int(self.times.shape[0])

    def index_at(self, t: float) -> int:
        if self.n == 0:
            return 0
        return int(min(self.n - 1, max(0, round(t / self.hop))))

    def slice_mean(self, name: str, start: float, end: float) -> float:
        arr = getattr(self, name, None)
        if arr is None or self.n == 0:
            return 0.0
        i0, i1 = self.index_at(start), max(self.index_at(end), self.index_at(start) + 1)
        seg = np.asarray(arr[i0:i1], dtype=np.float32)
        return float(seg.mean()) if seg.size else 0.0

    def slice_max(self, name: str, start: float, end: float) -> float:
        arr = getattr(self, name, None)
        if arr is None or self.n == 0:
            return 0.0
        i0, i1 = self.index_at(start), max(self.index_at(end), self.index_at(start) + 1)
        seg = np.asarray(arr[i0:i1], dtype=np.float32)
        return float(seg.max()) if seg.size else 0.0


def _frame_view(x: np.ndarray, win: int, hop: int) -> np.ndarray:
    """חלוקה לפריימים חופפים בלי העתקת זיכרון."""
    if x.shape[0] < win:
        return np.zeros((0, win), dtype=x.dtype)
    n = 1 + (x.shape[0] - win) // hop
    stride = x.strides[0]
    return np.lib.stride_tricks.as_strided(
        x, shape=(n, win), strides=(stride * hop, stride), writeable=False
    )


def analyze_audio(
    wav_path: str | Path,
    *,
    hop_seconds: float = 0.1,
    win_seconds: float = 0.064,
    block_seconds: float = 30.0,
    on_progress: ProgressFn = None,
    cancel_event: Optional[threading.Event] = None,
) -> AudioFeatures:
    """מנתח קובץ WAV PCM 16-bit מונו. מחזיר AudioFeatures על רשת זמן אחידה."""
    path = Path(wav_path)
    if not path.exists():
        raise NoAudioError(message_key="processing.audio.missing")

    try:
        wf = wave.open(str(path), "rb")
    except (wave.Error, EOFError) as exc:
        raise NoAudioError(detail=str(exc)) from exc

    with wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        total_frames = wf.getnframes()

        if sampwidth != 2:
            raise PolixorError(message_key="processing.audio.bad_format")
        if total_frames <= 0:
            raise NoAudioError(message_key="processing.audio.empty")

        duration = total_frames / float(sr)
        hop = max(1, int(round(hop_seconds * sr)))
        win = max(hop, int(round(win_seconds * sr)))
        # מעגלים את החלון לחזקת 2 עבור FFT יעיל
        nfft = 1 << int(math.ceil(math.log2(win)))
        window = np.hanning(win).astype(np.float32)

        block_frames = max(win, int(block_seconds * sr))
        # מיישרים את גודל הבלוק לכפולה של hop כדי שהפריימים יתיישרו בין בלוקים
        block_frames = ((block_frames - win) // hop) * hop + win

        rms_list: list[np.ndarray] = []
        flux_list: list[np.ndarray] = []
        cent_list: list[np.ndarray] = []
        zcr_list: list[np.ndarray] = []

        carry = np.zeros(0, dtype=np.float32)
        prev_mag: Optional[np.ndarray] = None
        read_frames = 0
        freqs = np.fft.rfftfreq(nfft, d=1.0 / sr).astype(np.float32)

        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelledError()

            raw = wf.readframes(block_frames)
            if not raw:
                break
            read_frames += len(raw) // (sampwidth * channels)

            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if channels > 1:
                usable = (data.shape[0] // channels) * channels
                data = data[:usable].reshape(-1, channels).mean(axis=1)

            buf = np.concatenate([carry, data]) if carry.size else data
            frames = _frame_view(np.ascontiguousarray(buf), win, hop)
            if frames.shape[0]:
                consumed = (frames.shape[0] - 1) * hop + win
                carry = buf[consumed - (win - hop):].copy() if consumed >= (win - hop) else buf.copy()

                wf_frames = frames * window
                rms = np.sqrt(np.maximum(1e-12, (wf_frames ** 2).mean(axis=1)))
                rms_list.append(rms.astype(np.float32))

                spec = np.abs(np.fft.rfft(wf_frames, n=nfft, axis=1)).astype(np.float32)
                mag_sum = spec.sum(axis=1) + 1e-9
                cent_list.append((spec @ freqs) / mag_sum)

                norm = spec / mag_sum[:, None]
                if prev_mag is not None:
                    prev_row = np.vstack([prev_mag[None, :], norm[:-1]])
                else:
                    prev_row = np.vstack([norm[0][None, :], norm[:-1]])
                diff = norm - prev_row
                flux_list.append(np.sqrt((np.maximum(diff, 0.0) ** 2).sum(axis=1)).astype(np.float32))
                prev_mag = norm[-1]

                signs = np.signbit(frames)
                zcr_list.append(
                    (np.diff(signs, axis=1).sum(axis=1) / float(win)).astype(np.float32)
                )
            else:
                carry = buf.copy()

            if on_progress and total_frames:
                on_progress(min(0.99, read_frames / total_frames))

    if not rms_list:
        raise NoAudioError(message_key="processing.audio.too_short")

    rms = np.concatenate(rms_list)
    flux = np.concatenate(flux_list)
    centroid = np.concatenate(cent_list)
    zcr = np.concatenate(zcr_list)
    n = int(min(rms.size, flux.size, centroid.size, zcr.size))
    rms, flux, centroid, zcr = rms[:n], flux[:n], centroid[:n], zcr[:n]

    hop_s = hop / float(sr)
    times = (np.arange(n, dtype=np.float32) * hop_s).astype(np.float32)
    rms_db = (20.0 * np.log10(np.maximum(rms, 1e-7))).astype(np.float32)

    feats = AudioFeatures(sample_rate=sr, hop=hop_s, duration=duration,
                          times=times, rms_db=rms_db, flux=flux,
                          centroid=centroid, zcr=zcr)
    _derive(feats)
    if on_progress:
        on_progress(1.0)
    log.info("audio features: %d frames, %.1fs, noise floor %.1f dB",
             n, duration, feats.noise_floor_db)
    return feats


def _derive(f: AudioFeatures) -> None:
    """גזירת אותות מסדר שני: רצפת רעש, אנרגיה מנורמלת, קפיצות, שקט, צחוק."""
    db = f.rms_db
    if db.size == 0:
        return

    # רצפת רעש = אחוזון 10 של העוצמה; שיא = אחוזון 99 (עמיד לקליקים)
    noise = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 99))
    f.noise_floor_db = noise
    f.peak_db = peak
    span = max(6.0, peak - noise)

    f.energy = np.clip((db - noise) / span, 0.0, 1.0).astype(np.float32)
    f.silence = (db < (noise + 6.0))
    f.speech_ratio = float(1.0 - f.silence.mean()) if f.silence.size else 0.0

    # קפיצת עוצמה: עוצמה נוכחית מול חציון של חלון רקע קודם (~6 שניות)
    bg_frames = max(3, int(round(6.0 / max(1e-6, f.hop))))
    baseline = _rolling_percentile(db, bg_frames, 50.0)
    jump_db = db - baseline
    f.jump = np.clip(jump_db / 12.0, 0.0, 1.0).astype(np.float32)

    # נרמול flux מול אחוזון 95 כדי להיות עמיד לפיקים בודדים
    fl_ref = float(np.percentile(f.flux, 95)) or 1.0
    f.flux = np.clip(f.flux / fl_ref, 0.0, 1.5).astype(np.float32)

    # צחוק/המולה: אנרגיה גבוהה + מודולציה מהירה של העוצמה (4-8 הרץ)
    f.laughter = _laughter_proxy(f)


def _rolling_percentile(x: np.ndarray, window: int, q: float) -> np.ndarray:
    """
    אחוזון נע מקורב: מחשבים על רשת גסה ומיישרים באינטרפולציה.
    זה מהיר בהרבה מחלון מלא ומספיק מדויק לצורך זיהוי קפיצות.
    """
    n = x.size
    if n == 0:
        return x.copy()
    step = max(1, window // 2)
    idx = np.arange(0, n, step)
    vals = np.empty(idx.size, dtype=np.float32)
    for k, i in enumerate(idx):
        lo = max(0, i - window)
        vals[k] = np.percentile(x[lo:i + 1], q) if i > lo else x[i]
    if idx.size == 1:
        return np.full(n, vals[0], dtype=np.float32)
    return np.interp(np.arange(n), idx, vals).astype(np.float32)


def _laughter_proxy(f: AudioFeatures) -> np.ndarray:
    """
    היוריסטיקה לצחוק/תגובה קולית: אנרגיה מעל הממוצע יחד עם
    מודולציית עוצמה מהירה (צחוק הוא סדרת פרצים ב-4-8 הרץ).
    זהו אות תומך – לא מסווג צחוק ודאי.
    """
    if f.energy.size < 8:
        return np.zeros_like(f.energy)

    e = f.energy.astype(np.float32)
    # אנרגיית תנודה: סטיית תקן בחלון קצר של הנגזרת
    d = np.abs(np.diff(e, prepend=e[0]))
    win = max(3, int(round(0.5 / max(1e-6, f.hop))))
    kernel = np.ones(win, dtype=np.float32) / win
    mod = np.convolve(d, kernel, mode="same")
    mod_ref = float(np.percentile(mod, 95)) or 1.0
    mod_n = np.clip(mod / mod_ref, 0.0, 1.0)

    # מרכז ספקטרלי גבוה יחסית תומך בצחוק/צווחה מול דיבור רגיל
    c = f.centroid
    c_ref = float(np.percentile(c, 80)) or 1.0
    c_n = np.clip(c / c_ref, 0.0, 1.5) - 0.6
    c_n = np.clip(c_n, 0.0, 1.0)

    return np.clip(0.55 * mod_n * e + 0.45 * c_n * e, 0.0, 1.0).astype(np.float32)


def integrated_loudness(wav_path: str | Path) -> Optional[float]:
    """
    LUFS משולב דרך ffmpeg loudnorm. מוחזר None אם לא ניתן למדוד.
    משמש להחלטה אם לאזן עוצמות בייצוא.
    """
    import json as _json
    import re as _re
    import subprocess

    from ..util.ffmpeg import ffmpeg_bin

    try:
        res = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(wav_path),
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=1800,
        )
        m = _re.search(r"\{[^{}]*input_i[^{}]*\}", res.stderr, _re.S)
        if not m:
            return None
        return float(_json.loads(m.group(0)).get("input_i"))
    except Exception:
        return None
