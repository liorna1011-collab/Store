"""
מאסטרינג אודיו: מודדים קודם, מחליטים לפי המדידה, ומודדים שוב אחרי.

העיקרון שמנחה את הקובץ הזה: **עיבוד אודיו הוא הרסני.** הפחתת רעש
מרככת את הקול, דחיסה מוחקת דינמיקה, ולימיטר חותך פסגות. לכן כל
שלב כאן מופעל רק כשהמדידה מראה שהוא נחוץ, וכשהמקור כבר טוב
הרשרשת נשארת ריקה — זה לא כישלון אלא התוצאה הנכונה.

הזרימה:

    measure(src)            →  AudioMeasurement   (לפני)
    plan_mastering(m, …)    →  MasteringPlan      (החלטות + רשרשת)
    master(src, dst, plan)  →  MasteringResult    (מריץ, מודד שוב)

המדידה נשענת על שני מקורות שמשלימים זה את זה:
  • `loudnorm` של FFmpeg — LUFS משולב, שיא אמיתי ו-LRA, לפי EBU R128.
  • ניתוח numpy על הדגימות — רצפת רעש, יחס שתיקה, קליפינג, ורעש
    תדר נמוך. אלה דברים ש-loudnorm לא מדווח עליהם.

מה **לא** נעשה כאן: קליפינג שכבר קיים במקור אינו ניתן לתיקון.
המערכת מזהה אותו ומדווחת עליו, ולא מתיימרת „לשקם" אותו.
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .. import i18n
from ..util.ffmpeg import extract_audio_wav, ffmpeg_bin

log = logging.getLogger("polixor.mastering")


# --------------------------------------------------------------------------
# יעדי עוצמה
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class LoudnessTarget:
    name: str
    lufs: float          # עוצמה משולבת מבוקשת
    true_peak: float     # תקרת שיא אמיתי, dBTP
    lra: float           # טווח דינמי מבוקש

    @property
    def label(self) -> str:
        return i18n.tr(f"mastering.target.{self.name}", default=self.name)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "lufs": self.lufs,
                "true_peak": self.true_peak, "lra": self.lra}


TARGETS: dict[str, LoudnessTarget] = {
    # הפלטפורמות החברתיות מנרמלות בעצמן לסביבות ‎-14 LUFS; חריגה
    # כלפי מעלה רק גורמת להן להנמיך בחזרה, ואיתה הדינמיקה נעלמת.
    "social": LoudnessTarget("social", -14.0, -1.0, 11.0),
    "podcast": LoudnessTarget("podcast", -16.0, -1.0, 11.0),
    "broadcast": LoudnessTarget("broadcast", -23.0, -2.0, 15.0),
}
DEFAULT_TARGET = "social"


def get_target(name: Optional[str]) -> LoudnessTarget:
    return TARGETS.get((name or "").strip().lower(), TARGETS[DEFAULT_TARGET])


# --------------------------------------------------------------------------
# ספים — כל אחד מהם הוא הבסיס להחלטה אחת
# --------------------------------------------------------------------------
DIGITAL_SILENCE = 10.0 ** (-96.0 / 20.0)   # רצפת 16-bit
WINDOW_SECONDS = 0.05       # חלון המדידה
SILENCE_BELOW_SPEECH = 25.0 # dB מתחת לרמת הדיבור = הפסקה
MIN_SILENCE_FOR_FLOOR = 0.4 # שניות הפסקה שצריך כדי למדוד רעש בכלל
SNR_CLEAN = 30.0            # מעל זה: הרעש לא נשמע, לא נוגעים
SNR_NOISY = 20.0            # מתחת לזה: רעש רקע מורגש
LRA_EVEN = 6.0              # מתחת לזה: העוצמה כבר אחידה
LRA_WIDE = 11.0             # מעל לזה: קופצים בין לחישה לצעקה
CREST_COMPRESS = 16.0       # dB בין שיא ל-RMS — מעל זה דחיסה עוזרת
LUFS_TOLERANCE = 1.0        # סטייה שלא שווה לגעת בה
CLIP_RATIO_BAD = 1e-4       # שיעור דגימות חתוכות שכבר נשמע
RUMBLE_RATIO = 0.06         # שיעור אנרגיה מתחת ל-100Hz
MAX_NOISE_REDUCTION = 12.0  # dB. מעבר לזה הקול נשמע „מתכתי"
SILENCE_FOR_GATE = 0.18     # שיעור שתיקה שמצדיק שער רעש


# --------------------------------------------------------------------------
# מדידה
# --------------------------------------------------------------------------
@dataclass
class AudioMeasurement:
    """מה שנמדד בפועל. `ok=False` פירושו שהמדידה עצמה לא הצליחה."""

    ok: bool = False
    error: str = ""
    duration: float = 0.0
    sample_rate: int = 0
    # EBU R128 (מ-loudnorm)
    lufs: Optional[float] = None
    true_peak: Optional[float] = None
    lra: Optional[float] = None
    threshold: Optional[float] = None
    # מדידות דגימה (numpy)
    peak_db: Optional[float] = None
    rms_db: Optional[float] = None
    speech_level_db: Optional[float] = None
    speech_level_hp_db: Optional[float] = None
    # רצפת הרעש נמדדת **רק בהפסקות**. בלי הפסקות אין מה למדוד,
    # והערך נשאר None — „לא ידוע" ולא ניחוש.
    noise_floor_db: Optional[float] = None
    # אותה מדידה אחרי סינון תדר נמוך: זה מה שהפחתת הרעש תפגוש
    # בפועל, כי הסינון רץ לפניה.
    noise_floor_hp_db: Optional[float] = None
    silence_seconds: float = 0.0
    crest_db: Optional[float] = None
    silence_ratio: Optional[float] = None
    clipped_samples: int = 0
    clip_ratio: float = 0.0
    low_band_ratio: Optional[float] = None

    @property
    def is_silent(self) -> bool:
        return self.peak_db is not None and self.peak_db < -60.0

    @property
    def is_clipped(self) -> bool:
        return self.clip_ratio >= CLIP_RATIO_BAD

    @property
    def snr_db(self) -> Optional[float]:
        """
        כמה הדיבור חזק מרעש הרקע. זה מה שקובע אם הרעש נשמע —
        ולא הערך המוחלט של רצפת הרעש: רצפה של ‎-30dBFS היא שקטה
        מתחת לדיבור חזק ורועשת מתחת ללחישה.
        """
        if self.noise_floor_db is None or self.speech_level_db is None:
            return None
        return float(self.speech_level_db - self.noise_floor_db)

    def to_dict(self) -> dict[str, Any]:
        def r(v, n=2):
            return None if v is None else round(float(v), n)

        return {
            "ok": self.ok, "error": self.error,
            "duration": r(self.duration), "sample_rate": self.sample_rate,
            "lufs": r(self.lufs), "true_peak": r(self.true_peak),
            "lra": r(self.lra), "threshold": r(self.threshold),
            "peak_db": r(self.peak_db), "rms_db": r(self.rms_db),
            "speech_level_db": r(self.speech_level_db),
            "speech_level_hp_db": r(self.speech_level_hp_db),
            "noise_floor_db": r(self.noise_floor_db),
            "noise_floor_hp_db": r(self.noise_floor_hp_db),
            "snr_db": r(self.snr_db),
            "crest_db": r(self.crest_db),
            "silence_ratio": r(self.silence_ratio, 3),
            "silence_seconds": r(self.silence_seconds),
            "clipped_samples": self.clipped_samples,
            "clip_ratio": round(self.clip_ratio, 6),
            "low_band_ratio": r(self.low_band_ratio, 3),
        }


def measure(src: str | Path, *, timeout: float = 1800.0) -> AudioMeasurement:
    """
    מודד את האודיו של קובץ. לא משנה אותו.

    מחזיר `ok=False` עם הסבר אם אין פס קול או שהמדידה נכשלה —
    ולא ערכים מומצאים.
    """
    path = Path(src)
    if not path.exists():
        return AudioMeasurement(ok=False, error=i18n.tr("mastering.measure.missing"))

    with tempfile.TemporaryDirectory(prefix="pxmaster_") as tmp:
        wav = Path(tmp) / "probe.wav"
        try:
            extract_audio_wav(path, wav, sample_rate=48000, channels=1)
        except Exception as exc:                        # noqa: BLE001
            return AudioMeasurement(ok=False,
                                    error=i18n.tr("mastering.measure.no_audio_detail", error=exc))
        if not wav.exists() or wav.stat().st_size < 1024:
            return AudioMeasurement(ok=False, error=i18n.tr("mastering.measure.no_audio"))

        m = _measure_samples(wav)
        r128 = _measure_r128(wav, timeout=timeout)
        if r128:
            m.lufs = r128.get("input_i")
            m.true_peak = r128.get("input_tp")
            m.lra = r128.get("input_lra")
            m.threshold = r128.get("input_thresh")
        m.ok = True
    return m


def _measure_r128(wav: Path, *, timeout: float) -> dict[str, float]:
    """LUFS / true peak / LRA דרך loudnorm במעבר ניתוח."""
    try:
        res = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(wav),
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout)
        match = re.search(r"\{[^{}]*input_i[^{}]*\}", res.stderr, re.S)
        if not match:
            return {}
        raw = json.loads(match.group(0))
    except Exception as exc:                            # noqa: BLE001
        log.debug("loudnorm measurement failed: %s", exc)
        return {}

    out: dict[str, float] = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh"):
        try:
            val = float(raw.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(val):
            out[key] = val
    return out


def _measure_samples(wav: Path) -> AudioMeasurement:
    """
    מדידות שדורשות גישה לדגימות עצמן.

    רצפת הרעש היא האחוזון העשירי של עוצמת החלונות — כלומר מה
    שנשמע בהפסקות בין המשפטים, ולא ממוצע כללי שהדיבור מושך
    כלפי מעלה.
    """
    m = AudioMeasurement()
    try:
        with wave.open(str(wav), "rb") as wf:
            m.sample_rate = wf.getframerate()
            frames = wf.getnframes()
            raw = wf.readframes(frames)
    except Exception as exc:                            # noqa: BLE001
        m.error = i18n.tr("mastering.measure.read_failed", error=exc)
        return m

    if not raw:
        m.error = i18n.tr("mastering.measure.empty")
        return m

    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if x.size == 0:
        m.error = i18n.tr("mastering.measure.empty")
        return m
    m.duration = x.size / float(m.sample_rate or 48000)

    # קליפינג: לא דגימה חזקה בודדת אלא רצף שטוח בקצה הסקאלה
    at_ceiling = np.abs(x) >= 0.999
    m.clipped_samples = int(_run_length_count(at_ceiling, min_run=3))
    m.clip_ratio = m.clipped_samples / float(x.size)

    peak = float(np.max(np.abs(x)))
    m.peak_db = _db(peak)
    m.rms_db = _db(float(np.sqrt(np.mean(np.square(x)))))

    sr = m.sample_rate or 48000
    win = max(256, int(sr * WINDOW_SECONDS))
    stats = _window_stats(x, win, sr)
    m.speech_level_db = stats.speech_db
    m.noise_floor_db = stats.floor_db
    m.silence_ratio = stats.silence_ratio
    m.silence_seconds = stats.silence_seconds

    if m.peak_db is not None and m.rms_db is not None:
        m.crest_db = float(m.peak_db - m.rms_db)

    # ניתוח התדר הנמוך פעם אחת, ומתוכו גם האות המסונן — כדי
    # שהפחתת הרעש תימדד מול מה שהיא באמת תפגוש אחרי הסינון.
    analysis = x[: sr * 60] if x.size > sr * 60 else x
    m.low_band_ratio, hp = _split_low_band(analysis, sr)
    if hp is not None:
        hp_stats = _window_stats(hp, win, sr)
        m.noise_floor_hp_db = hp_stats.floor_db
        m.speech_level_hp_db = hp_stats.speech_db
    else:
        m.noise_floor_hp_db = m.noise_floor_db
        m.speech_level_hp_db = m.speech_level_db
    return m


@dataclass
class _WindowStats:
    speech_db: Optional[float] = None
    floor_db: Optional[float] = None
    silence_ratio: float = 0.0
    silence_seconds: float = 0.0


def _window_stats(x: np.ndarray, win: int, sr: int) -> _WindowStats:
    """
    רמת הדיבור ורצפת הרעש, מתוך התפלגות עוצמת החלונות.

    הנקודה העדינה: רצפת הרעש היא מה שנשמע **בהפסקות**, ולא
    „החלק השקט ביותר של האודיו". על דיבור רצוף בלי הפסקות,
    אחוזון נמוך מודד דיבור חלש ולא רעש — ואז המערכת ״מגלה״
    רעש בקובץ נקי לחלוטין ומפעילה עליו הפחתת רעש מיותרת.

    לכן: מזהים הפסקות ביחס לרמת הדיבור, ורק אם יש מספיק מהן
    מחזירים רצפת רעש. אחרת הערך נשאר None — „לא נמדד".
    """
    out = _WindowStats()
    n_win = x.size // win
    if n_win < 8:
        return out
    blocks = x[:n_win * win].reshape(n_win, win)
    win_rms = np.sqrt(np.mean(np.square(blocks), axis=1))
    # רצפת 16-bit היא בערך ‎-96dBFS. מתחת לזה אין מידע, ולכן אין
    # טעם לדווח מספר „מדויק" יותר — זו שתיקה דיגיטלית.
    win_db = 20.0 * np.log10(np.maximum(win_rms, DIGITAL_SILENCE))

    out.speech_db = float(np.percentile(win_db, 95))
    quiet = win_db < (out.speech_db - SILENCE_BELOW_SPEECH)
    out.silence_ratio = float(np.mean(quiet))
    out.silence_seconds = float(quiet.sum() * win / float(sr))

    if out.silence_seconds >= MIN_SILENCE_FOR_FLOOR:
        # החציון של ההפסקות, ולא המינימום: דגימה בודדת שקטה
        # במיוחד אינה מייצגת את רעש הרקע.
        out.floor_db = float(np.median(win_db[quiet]))
    return out


def _run_length_count(mask: np.ndarray, *, min_run: int) -> int:
    """סופר דגימות ששייכות לרצף רצוף באורך `min_run` לפחות."""
    if mask.size == 0 or not mask.any():
        return 0
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts, ends = edges[0::2], edges[1::2]
    lengths = ends - starts
    return int(lengths[lengths >= min_run].sum())


def _split_low_band(x: np.ndarray, sr: int, cutoff: float = 100.0
                    ) -> tuple[float, Optional[np.ndarray]]:
    """
    מפריד את התדר הנמוך מהשאר, ומחזיר (שיעור האנרגיה מתחת ל-cutoff,
    האות בלי התדר הנמוך).

    רעידות שולחן, מזגן ורעש רצפה יושבים מתחת ל-100Hz והם לא חלק
    מהקול האנושי. האות המסונן מוחזר כדי שאפשר יהיה למדוד עליו את
    רצפת הרעש האמיתית שהפחתת הרעש תפגוש.
    """
    n = x.size
    if n < 1024:
        return 0.0, None
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    power = np.abs(spec) ** 2
    total = float(power.sum())
    low_mask = freqs < cutoff
    ratio = float(power[low_mask].sum() / total) if total > 0 else 0.0

    spec_hp = spec.copy()
    spec_hp[low_mask] = 0.0
    hp = np.fft.irfft(spec_hp, n=n).astype(np.float32)
    return ratio, hp


def _db(x: float) -> float:
    return -120.0 if x <= 1e-6 else float(20.0 * math.log10(x))


# --------------------------------------------------------------------------
# החלטות
# --------------------------------------------------------------------------
@dataclass
class MasteringStep:
    """שלב אחד, עם התשובה לשאלה „למה כן" או „למה לא"."""

    action: str          # highpass | denoise | gate | level | compress |
                         # normalize | limit
    applied: bool
    reason: str
    filter: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "applied": self.applied,
                "reason": self.reason, "filter": self.filter,
                "params": self.params}


@dataclass
class MasteringPlan:
    before: AudioMeasurement
    target: LoudnessTarget
    steps: list[MasteringStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def applied_steps(self) -> list[MasteringStep]:
        return [s for s in self.steps if s.applied]

    @property
    def is_noop(self) -> bool:
        return not self.applied_steps

    def filter_chain(self) -> str:
        return ",".join(s.filter for s in self.applied_steps if s.filter)

    def to_dict(self) -> dict[str, Any]:
        return {
            "before": self.before.to_dict(),
            "target": self.target.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "filter_chain": self.filter_chain(),
            "is_noop": self.is_noop,
            "notes": self.notes,
            "warnings": self.warnings,
        }


def plan_mastering(m: AudioMeasurement, *,
                   target: LoudnessTarget | str = DEFAULT_TARGET,
                   allow_denoise: bool = True,
                   allow_compress: bool = True) -> MasteringPlan:
    """
    בונה תכנית לפי המדידה בלבד. לא מריץ כלום.

    כל שלב מופיע בתכנית גם כשהוא לא הופעל, עם ההסבר למה — כדי
    שהמשתמש יראה מה נשקל ולא רק מה נעשה.
    """
    tgt = target if isinstance(target, LoudnessTarget) else get_target(target)
    plan = MasteringPlan(before=m, target=tgt)

    if not m.ok:
        plan.warnings.append(m.error or i18n.tr("mastering.plan.unmeasured"))
        plan.notes.append(i18n.tr("mastering.plan.no_basis"))
        return plan

    if m.is_silent:
        plan.warnings.append(i18n.tr("mastering.plan.silent"))
        return plan

    if m.is_clipped:
        plan.warnings.append(i18n.tr("mastering.plan.clipped",
                                     samples=f"{m.clipped_samples:,}",
                                     percent=f"{m.clip_ratio * 100:.3f}"))

    _step_highpass(plan, m)
    _step_denoise(plan, m, allow_denoise)
    _step_gate(plan, m)
    _step_level(plan, m)
    _step_compress(plan, m, allow_compress)
    _step_normalize(plan, m, tgt)
    _step_limit(plan, m, tgt)

    if plan.is_noop:
        plan.notes.append(i18n.tr("mastering.plan.noop"))
    return plan


def _step_highpass(plan: MasteringPlan, m: AudioMeasurement) -> None:
    ratio = m.low_band_ratio if m.low_band_ratio is not None else 0.0
    if ratio >= RUMBLE_RATIO:
        plan.steps.append(MasteringStep(
            "highpass", True,
            i18n.tr("mastering.highpass.on", percent=f"{ratio * 100:.0f}"),
            "highpass=f=80:poles=2", {"cutoff": 80, "low_band_ratio": ratio}))
    else:
        plan.steps.append(MasteringStep(
            "highpass", False,
            i18n.tr("mastering.highpass.off", percent=f"{ratio * 100:.0f}"),
            "", {"low_band_ratio": ratio}))


def _step_denoise(plan: MasteringPlan, m: AudioMeasurement,
                  allowed: bool) -> None:
    if not allowed:
        plan.steps.append(MasteringStep(
            "denoise", False, i18n.tr("mastering.denoise.disabled"), ""))
        return

    # אם סינון התדר הנמוך רץ לפנינו, הרעש שהוא מסיר כבר לא קיים.
    # מדידה על האות הלא-מסונן הייתה מנפחת את עוצמת ההפחתה — ורעידות
    # רציפות גם מסתירות את ההפסקות עצמן, ולכן על האות המסונן יש
    # לפעמים מה למדוד דווקא כשעל המקורי אין.
    filtered = any(s.action == "highpass" and s.applied for s in plan.steps)
    if filtered and m.noise_floor_hp_db is not None:
        floor = m.noise_floor_hp_db
        speech = m.speech_level_hp_db or m.speech_level_db
        basis = i18n.tr("mastering.denoise.after_highpass")
    else:
        floor = m.noise_floor_db
        speech = m.speech_level_db
        basis = ""

    # בלי הפסקות אין איפה למדוד רעש רקע. במצב כזה לא מנחשים:
    # הפחתת רעש על סמך ניחוש מרככת קול תקין לגמרי.
    if floor is None or speech is None:
        plan.steps.append(MasteringStep(
            "denoise", False,
            i18n.tr("mastering.denoise.no_pauses", seconds=f"{m.silence_seconds:.1f}"), "",
            {"silence_seconds": m.silence_seconds}))
        return

    snr = float(speech - floor)

    if snr >= SNR_CLEAN:
        plan.steps.append(MasteringStep(
            "denoise", False,
            i18n.tr("mastering.denoise.clean", snr=f"{snr:.0f}", basis=basis), "",
            {"snr_db": snr, "noise_floor_db": floor,
             "after_highpass": filtered}))
        return

    # ככל שה-SNR נמוך יותר מפחיתים יותר, אבל לא מעבר לתקרה:
    # הפחתה אגרסיבית נשמעת גרוע יותר מהרעש עצמו.
    deficit = SNR_CLEAN - snr
    nr = float(min(MAX_NOISE_REDUCTION, max(4.0, deficit * 0.8)))
    strong = snr <= SNR_NOISY
    plan.steps.append(MasteringStep(
        "denoise", True,
        i18n.tr("mastering.denoise.on", snr=f"{snr:.0f}", basis=basis,
                severity=i18n.tr("mastering.denoise.strong" if strong
                                 else "mastering.denoise.light"),
                reduction=f"{nr:.0f}"),
        f"afftdn=nr={nr:.0f}:nf={floor - 4:.0f}:tn=1",
        {"snr_db": snr, "noise_floor_db": floor, "reduction_db": nr,
         "after_highpass": filtered}))


def _step_gate(plan: MasteringPlan, m: AudioMeasurement) -> None:
    snr = m.snr_db
    floor = m.noise_floor_db
    silence = m.silence_ratio if m.silence_ratio is not None else 0.0
    if floor is None or snr is None:
        plan.steps.append(MasteringStep(
            "gate", False,
            i18n.tr("mastering.gate.unmeasured"),
            "", {"silence_ratio": silence}))
        return
    if snr <= SNR_NOISY and silence >= SILENCE_FOR_GATE:
        thr = max(-60.0, floor + 3.0)
        plan.steps.append(MasteringStep(
            "gate", True,
            i18n.tr("mastering.gate.on", percent=f"{silence * 100:.0f}"),
            f"agate=threshold={_lin(thr):.5f}:ratio=2:attack=20:release=250",
            {"threshold_db": thr, "silence_ratio": silence}))
    else:
        plan.steps.append(MasteringStep(
            "gate", False,
            i18n.tr("mastering.gate.off"), "",
            {"silence_ratio": silence, "noise_floor_db": floor,
             "snr_db": snr}))


def _step_level(plan: MasteringPlan, m: AudioMeasurement) -> None:
    lra = m.lra
    if lra is None:
        plan.steps.append(MasteringStep(
            "level", False, i18n.tr("mastering.level.unmeasured"), ""))
        return
    if lra <= LRA_EVEN:
        plan.steps.append(MasteringStep(
            "level", False,
            i18n.tr("mastering.level.even", lra=f"{lra:.1f}"), "",
            {"lra": lra}))
        return
    strength = i18n.tr("mastering.level.strong" if lra >= LRA_WIDE
                       else "mastering.level.moderate")
    g = 11 if lra >= LRA_WIDE else 15
    plan.steps.append(MasteringStep(
        "level", True,
        i18n.tr("mastering.level.on", lra=f"{lra:.1f}", strength=strength),
        f"dynaudnorm=f=200:g={g}:p=0.9:m=8:s=10", {"lra": lra}))


def _step_compress(plan: MasteringPlan, m: AudioMeasurement,
                   allowed: bool) -> None:
    crest = m.crest_db
    if not allowed:
        plan.steps.append(MasteringStep(
            "compress", False, i18n.tr("mastering.compress.disabled"), ""))
        return
    if crest is None:
        plan.steps.append(MasteringStep(
            "compress", False, i18n.tr("mastering.compress.unmeasured"), ""))
        return
    if crest <= CREST_COMPRESS:
        plan.steps.append(MasteringStep(
            "compress", False,
            i18n.tr("mastering.compress.dense", crest=f"{crest:.0f}"), "",
            {"crest_db": crest}))
        return
    thr = -20.0 if crest > CREST_COMPRESS + 6 else -17.0
    plan.steps.append(MasteringStep(
        "compress", True,
        i18n.tr("mastering.compress.on", crest=f"{crest:.0f}"),
        f"acompressor=threshold={thr}dB:ratio=2.5:attack=12:release=180"
        ":makeup=1.5", {"crest_db": crest, "threshold_db": thr}))


def _step_normalize(plan: MasteringPlan, m: AudioMeasurement,
                    tgt: LoudnessTarget) -> None:
    if m.lufs is None:
        plan.steps.append(MasteringStep(
            "normalize", False,
            i18n.tr("mastering.normalize.unmeasured"), ""))
        return
    delta = tgt.lufs - m.lufs
    if abs(delta) <= LUFS_TOLERANCE:
        plan.steps.append(MasteringStep(
            "normalize", False,
            i18n.tr("mastering.normalize.close", lufs=f"{m.lufs:.1f}",
                    target=f"{tgt.lufs:.0f}", delta=f"{abs(delta):.1f}"), "",
            {"lufs": m.lufs, "target": tgt.lufs}))
        return

    # כמה הגבר נכנס מתחת לתקרת השיא. אם היעד דורש יותר מזה,
    # הגבר לינארי פשוט לא יכול להגיע אליו בלי לחתוך פסגות.
    headroom = (tgt.true_peak - m.true_peak
                if m.true_peak is not None else delta)
    if delta > headroom + 0.05:
        # loudnorm היה נופל כאן בשקט למצב דינמי ומחטיא את היעד.
        # במקום זה: הגבר מפורש + לימיטר שתופס את הפסגות. זה מה
        # שעושים באולפן, וכאן זה גם כתוב במפורש.
        plan.steps.append(MasteringStep(
            "normalize", True,
            i18n.tr("mastering.normalize.gain_limited", lufs=f"{m.lufs:.1f}",
                    target=f"{tgt.lufs:.0f}", label=tgt.label,
                    headroom=f"{headroom:.1f}", delta=f"{delta:.1f}"),
            f"volume={delta:.2f}dB",
            {"lufs": m.lufs, "target": tgt.lufs, "delta": round(delta, 2),
             "headroom": round(headroom, 2), "mode": "gain_limited"}))
        return

    # מעבר שני של loudnorm: כשמוסרים לו את המדידה הוא מחיל הגבר
    # לינארי במקום לדחוס דינמית. זו הדרך הלא-הרסנית לנרמל.
    params = [f"I={tgt.lufs}", f"TP={tgt.true_peak}", f"LRA={tgt.lra}",
              f"measured_I={m.lufs:.2f}"]
    if m.true_peak is not None:
        params.append(f"measured_TP={m.true_peak:.2f}")
    if m.lra is not None:
        params.append(f"measured_LRA={m.lra:.2f}")
    if m.threshold is not None:
        params.append(f"measured_thresh={m.threshold:.2f}")
    params += ["linear=true", "print_format=summary"]
    plan_mode = {"mode": "linear"}

    direction = i18n.tr("mastering.normalize.up" if delta > 0
                        else "mastering.normalize.down")
    plan.steps.append(MasteringStep(
        "normalize", True,
        i18n.tr("mastering.normalize.linear", lufs=f"{m.lufs:.1f}",
                target=f"{tgt.lufs:.0f}", label=tgt.label, direction=direction,
                delta=f"{abs(delta):.1f}"),
        "loudnorm=" + ":".join(params),
        {"lufs": m.lufs, "target": tgt.lufs, "delta": round(delta, 2),
         **plan_mode}))


# שלבים שמשנים את העוצמה לפני הנרמול, ולכן מחייבים מדידה חוזרת
PRE_ACTIONS = ("highpass", "denoise", "gate", "level", "compress")
LOUDNESS_ACTIONS = ("normalize", "limit")

# `alimiter` מגביל שיא דגימה, לא שיא אמיתי. פסגות בין-דגימתיות
# יכולות לעלות מעליו בכחצי דציבל, ולכן מכוונים מעט מתחת לתקרה.
INTERSAMPLE_HEADROOM = 0.5


def plan_loudness(m: AudioMeasurement,
                  target: LoudnessTarget | str = DEFAULT_TARGET
                  ) -> MasteringPlan:
    """
    תכנית לשלב העוצמה בלבד — נרמול ולימיטר.

    משמשת במעבר השני: אחרי שהאיזון והדחיסה כבר שינו את העוצמה,
    ההגבר חייב להיגזר ממדידה של האות **כפי שהוא נכנס לשלב הזה**,
    ולא מהמדידה המקורית.
    """
    tgt = target if isinstance(target, LoudnessTarget) else get_target(target)
    plan = MasteringPlan(before=m, target=tgt)
    if not m.ok or m.is_silent:
        plan.warnings.append(m.error or i18n.tr("mastering.plan.nothing_to_normalize"))
        return plan
    _step_normalize(plan, m, tgt)
    _step_limit(plan, m, tgt)
    return plan


def _step_limit(plan: MasteringPlan, m: AudioMeasurement,
                tgt: LoudnessTarget) -> None:
    norm = next((s for s in plan.steps
                 if s.action == "normalize" and s.applied), None)
    tp = m.true_peak
    if norm is not None and norm.params.get("mode") == "gain_limited":
        # ההגבר לבדו היה מוציא את השיא מעל התקרה; הלימיטר הוא
        # החלק השני של אותה החלטה, ולכן הוא חייב לרוץ.
        plan.steps.append(MasteringStep(
            "limit", True,
            i18n.tr("mastering.limit.after_gain", ceiling=f"{tgt.true_peak:.0f}"),
            f"alimiter=limit={_lin(tgt.true_peak - INTERSAMPLE_HEADROOM):.5f}"
            ":level=disabled",
            {"ceiling": tgt.true_peak,
             "after_gain": round((tp or 0.0) + norm.params.get("delta", 0.0),
                                 2)}))
        return
    if norm is not None:
        plan.steps.append(MasteringStep(
            "limit", False,
            i18n.tr("mastering.limit.normalized", ceiling=f"{tgt.true_peak:.0f}"), "",
            {"true_peak_target": tgt.true_peak}))
        return
    if tp is None or tp <= tgt.true_peak:
        plan.steps.append(MasteringStep(
            "limit", False,
            i18n.tr("mastering.limit.below",
                    peak=(i18n.tr("mastering.limit.peak_unmeasured") if tp is None
                          else f"{tp:.1f}dBTP"),
                    ceiling=f"{tgt.true_peak:.0f}"), "",
            {"true_peak": tp}))
        return
    plan.steps.append(MasteringStep(
        "limit", True,
        i18n.tr("mastering.limit.on", peak=f"{tp:.1f}", ceiling=f"{tgt.true_peak:.0f}"),
        f"alimiter=limit={_lin(tgt.true_peak - INTERSAMPLE_HEADROOM):.5f}"
        ":level=disabled",
        {"true_peak": tp, "ceiling": tgt.true_peak}))


def _lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


# --------------------------------------------------------------------------
# ביצוע ואימות
# --------------------------------------------------------------------------
@dataclass
class MasteringResult:
    plan: MasteringPlan
    output: Optional[Path] = None
    after: Optional[AudioMeasurement] = None
    # מדידת הביניים, כשהעיבוד רץ בשני שלבים
    mid: Optional[AudioMeasurement] = None
    loudness_steps: list["MasteringStep"] = field(default_factory=list)
    processed: bool = False
    verified: bool = False
    needs_review: bool = False
    issues: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """שורה אחת לממשק — מה נעשה ומה יצא."""
        if not self.processed:
            if self.plan.warnings:
                return self.plan.warnings[0]
            return i18n.tr("mastering.result.noop")
        names = " · ".join(step_label(s.action) for s in self.plan.applied_steps)
        before = self.plan.before.lufs
        after = self.after.lufs if self.after else None
        if before is not None and after is not None:
            return i18n.tr("mastering.result.summary", steps=names,
                           before=f"{before:.1f}", after=f"{after:.1f}",
                           target=f"{self.plan.target.lufs:.0f}")
        return names

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "output": str(self.output) if self.output else None,
            "after": self.after.to_dict() if self.after else None,
            "mid": self.mid.to_dict() if self.mid else None,
            "loudness_steps": [s.to_dict() for s in self.loudness_steps],
            "processed": self.processed,
            "verified": self.verified,
            "needs_review": self.needs_review,
            "issues": self.issues,
            "summary": self.summary(),
        }


def step_label(action: str) -> str:
    """שם השלב בשפה הפעילה (הקטלוג: mastering.step.*)."""
    return i18n.tr(f"mastering.step.{action}", default=action)

# כמה מותר לפספס את היעד לפני שמסמנים „דורש בדיקה"
VERIFY_LUFS_TOLERANCE = 1.5
VERIFY_TP_TOLERANCE = 0.3


def master(src: str | Path, dst: str | Path, plan: MasteringPlan, *,
           timeout: float = 3600.0) -> MasteringResult:
    """
    מריץ את התכנית ומודד שוב את התוצאה.

    אם אין מה לעשות — לא נוצר קובץ חדש, והמקור נשאר כפי שהוא.
    זה לא כישלון: זו התוצאה הנכונה למקור טוב.

    אחרי העיבוד המדידה חוזרת. אם התוצאה לא הגיעה ליעד, הפעולה
    מסומנת `needs_review` ולא „הושלמה" — מספר שלא אומת לא מוצג
    כאילו אומת.
    """
    result = MasteringResult(plan=plan)
    if plan.is_noop:
        return result

    src_p, dst_p = Path(src), Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    pre = [s for s in plan.applied_steps if s.action in PRE_ACTIONS]
    loud = [s for s in plan.applied_steps if s.action in LOUDNESS_ACTIONS]

    # שני שלבים כשיש גם עיבוד שמשנה עוצמה וגם נרמול. איזון ודחיסה
    # מרימים את העוצמה בעצמם, ולכן הגבר שחושב מהמדידה המקורית
    # מחטיא — במקרה שנמדד, ב-4.6LU כלפי מעלה. לכן: מריצים את
    # העיבוד, **מודדים שוב**, ורק אז קובעים את ההגבר.
    if pre and loud:
        stage1 = dst_p.with_name(dst_p.stem + "_stage1" + dst_p.suffix)
        ok = _run_chain(src_p, stage1, ",".join(s.filter for s in pre),
                        result, timeout)
        if not ok:
            return result
        mid = measure(stage1, timeout=timeout)
        result.mid = mid
        loud_plan = plan_loudness(mid, plan.target)
        result.loudness_steps = list(loud_plan.steps)
        chain2 = loud_plan.filter_chain()
        if not chain2:
            # אחרי העיבוד העוצמה כבר ביעד — אין מה להוסיף
            stage1.replace(dst_p)
            plan.notes.append(i18n.tr("mastering.result.reached", lufs=f"{mid.lufs:.1f}"))
        else:
            ok = _run_chain(stage1, dst_p, chain2, result, timeout)
            stage1.unlink(missing_ok=True)
            if not ok:
                return result
            plan.notes.append(i18n.tr("mastering.result.second_pass", lufs=f"{mid.lufs:.1f}"))
    else:
        if not _run_chain(src_p, dst_p, plan.filter_chain(), result, timeout):
            return result

    result.processed = True
    result.output = dst_p
    result.after = measure(dst_p, timeout=timeout)
    _verify(result)
    return result


def _run_chain(src: Path, dst: Path, chain: str, result: "MasteringResult",
               timeout: float) -> bool:
    """מריץ מעבר עיבוד אחד. מחזיר False ומסמן את התקלה בכישלון."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-i", str(src),
           "-af", chain] + _output_codec_args(src, dst) + [str(dst)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout)
    except subprocess.TimeoutExpired:
        result.issues.append(i18n.tr("mastering.result.timeout"))
        result.needs_review = True
        return False
    if res.returncode != 0 or not dst.exists():
        tail = (res.stderr or "").strip().splitlines()[-3:]
        result.issues.append(i18n.tr("mastering.result.ffmpeg_failed", detail=" / ".join(tail)))
        result.needs_review = True
        return False
    return True


def _output_codec_args(src: Path, dst: Path) -> list[str]:
    """
    בוחר קודקים לפי מה שיש במקור ולפי המכל של היעד.

    שתי טעויות שהקוד הזה מונע: העתקת וידאו מקובץ שאין בו וידאו,
    וכתיבת AAC לתוך מכל WAV — שמפיקה קובץ שאי אפשר לקרוא בחזרה,
    ולכן גם אי אפשר למדוד את התוצאה.
    """
    lossless = {".wav": "pcm_s16le", ".flac": "flac"}
    suffix = dst.suffix.lower()

    has_video = False
    try:
        from ..util.ffmpeg import probe

        has_video = bool(probe(src).has_video)
    except Exception:                                   # noqa: BLE001
        has_video = src.suffix.lower() not in (
            ".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus")

    if suffix in lossless:
        # מכל ללא אבדן — אין בו וידאו, ורק קודק תואם ייקרא בחזרה
        return ["-vn", "-c:a", lossless[suffix]]

    args: list[str] = []
    if has_video:
        args += ["-c:v", "copy"]
    else:
        args += ["-vn"]
    return args + ["-c:a", "aac", "-b:a", "192k"]


def _verify(result: MasteringResult) -> None:
    """
    השוואת אחרי מול היעד. „exit code 0" אינו הוכחה לתוצאה נכונה.
    """
    after = result.after
    tgt = result.plan.target
    if after is None or not after.ok:
        result.issues.append(i18n.tr("mastering.verify.unmeasured"))
        result.needs_review = True
        return

    if after.is_silent:
        result.issues.append(i18n.tr("mastering.verify.silent"))
        result.needs_review = True
        return

    if after.lufs is None:
        result.issues.append(i18n.tr("mastering.verify.no_lufs"))
        result.needs_review = True
        return

    drift = abs(after.lufs - tgt.lufs)
    wanted_normalize = any(s.action == "normalize" and s.applied
                           for s in result.plan.steps)
    if wanted_normalize and drift > VERIFY_LUFS_TOLERANCE:
        result.issues.append(i18n.tr("mastering.verify.drift", lufs=f"{after.lufs:.1f}",
                                     target=f"{tgt.lufs:.0f}", drift=f"{drift:.1f}"))
        result.needs_review = True

    if (after.true_peak is not None
            and after.true_peak > tgt.true_peak + VERIFY_TP_TOLERANCE):
        result.issues.append(i18n.tr("mastering.verify.peak", peak=f"{after.true_peak:.1f}",
                                     ceiling=f"{tgt.true_peak:.0f}"))
        result.needs_review = True

    before_clip = result.plan.before.clip_ratio
    if after.clip_ratio > max(before_clip * 1.5, CLIP_RATIO_BAD):
        result.issues.append(i18n.tr("mastering.verify.clipping",
                                     after=f"{after.clip_ratio * 100:.3f}",
                                     before=f"{before_clip * 100:.3f}"))
        result.needs_review = True

    result.verified = not result.needs_review


def master_file(src: str | Path, dst: str | Path, *,
                target: str = DEFAULT_TARGET,
                allow_denoise: bool = True,
                allow_compress: bool = True) -> MasteringResult:
    """מדידה → תכנית → ביצוע → מדידה, בקריאה אחת."""
    before = measure(src)
    plan = plan_mastering(before, target=target, allow_denoise=allow_denoise,
                          allow_compress=allow_compress)
    return master(src, dst, plan)
