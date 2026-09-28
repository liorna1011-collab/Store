"""
היתוך אותות לציון עניין על ציר זמן אחיד.

עיקרון מרכזי: לא מסתמכים על צעקות בלבד. הציון נבנה מכמה **ערוצים**
עצמאיים, וציון סופי משלב גם סכום משוקלל וגם את הערוץ החזק ביותר –
כך שרגע שקט עם תוכן חזק, או רגע ויזואלי ללא דיבור, יכול לזכות בציון
גבוה גם אם הערוצים האחרים נמוכים.

ערוצים:
  vocal    – פרצי עוצמה, שינוי ספקטרלי, צחוק/המולה
  speech   – עניין לשוני: קצב דיבור, סימני שאלה/קריאה, מילות מפתח
  visual   – שינויי סצנה, תנועה, הבזקים
  pause    – שקט דרמטי שאחריו דיבור ("רגע, תקשיבו…")
  chat     – פעילות צ'אט (אופציונלי, רק כשיש הרשאה ונתונים)
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..config import AppSettings
from .audio import AudioFeatures
from .transcribe import TranscriptResult
from .visual import VisualFeatures

log = logging.getLogger("polixor.scoring")


# --------------------------------------------------------------------------
# לקסיקון עניין – מגיע מחבילות השפה (services/lang)
# --------------------------------------------------------------------------
from . import lang as _lang  # noqa: E402

# תאימות לאחור: הלקסיקונים הישנים עדיין זמינים בשמות הקודמים
LEX_HE: dict[str, float] = dict(_lang.HEBREW.lexicon)
LEX_EN: dict[str, float] = dict(_lang.ENGLISH.lexicon)

# תקרת התאמות לקסיקליות לטקסט אחד. בעבר ה-break עצר רק את הלולאה
# הפנימית, ולכן התקרה לא נאכפה בפועל.
MAX_LEXICAL_HITS = 6


@dataclass
class Timeline:
    """רשת זמן אחידה עם כל הערוצים."""

    hop: float = 0.1
    duration: float = 0.0
    times: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    vocal: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    speech: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    visual: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    pause: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    chat: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    score: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    speech_mask: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))

    @property
    def n(self) -> int:
        return int(self.times.shape[0])

    def idx(self, t: float) -> int:
        if self.n == 0:
            return 0
        return int(min(self.n - 1, max(0, round(t / self.hop))))

    def window_mean(self, arr: np.ndarray, start: float, end: float) -> float:
        i0, i1 = self.idx(start), max(self.idx(end), self.idx(start) + 1)
        seg = arr[i0:i1]
        return float(seg.mean()) if seg.size else 0.0

    def window_sum(self, arr: np.ndarray, start: float, end: float) -> float:
        i0, i1 = self.idx(start), max(self.idx(end), self.idx(start) + 1)
        seg = arr[i0:i1]
        return float(seg.sum()) if seg.size else 0.0

    def channel_breakdown(self, start: float, end: float) -> dict[str, float]:
        return {
            "vocal": round(self.window_mean(self.vocal, start, end), 4),
            "speech": round(self.window_mean(self.speech, start, end), 4),
            "visual": round(self.window_mean(self.visual, start, end), 4),
            "pause": round(self.window_mean(self.pause, start, end), 4),
            "chat": round(self.window_mean(self.chat, start, end), 4),
        }


def build_timeline(
    *,
    audio: Optional[AudioFeatures],
    visual: Optional[VisualFeatures],
    transcript: Optional[TranscriptResult],
    duration: float,
    settings: AppSettings,
    chat_events: Optional[list[tuple[float, float]]] = None,
    language: Optional[str] = None,
) -> Timeline:
    """
    בונה את ציר הזמן המשולב. כל רכיב חסר פשוט תורם אפס.

    `language` בוחר את חבילת השפה לניתוח הלשוני; None = כל החבילות.
    """
    hop = audio.hop if (audio and audio.n) else 0.1
    n = max(1, int(math.ceil(max(duration, 1.0) / hop)))
    times = (np.arange(n, dtype=np.float32) * hop).astype(np.float32)

    tl = Timeline(hop=hop, duration=duration, times=times)
    tl.vocal = _vocal_channel(audio, n)
    tl.visual = _visual_channel(visual, n, hop)
    tl.speech, tl.speech_mask = _speech_channel(transcript, n, hop, language=language)
    tl.pause = _pause_channel(audio, transcript, n, hop)
    tl.chat = _chat_channel(chat_events, n, hop) if (settings.use_chat_signal and chat_events) \
        else np.zeros(n, dtype=np.float32)

    tl.score = _fuse(tl, settings)
    return tl


# --------------------------------------------------------------------------
# ערוצים
# --------------------------------------------------------------------------
def _vocal_channel(audio: Optional[AudioFeatures], n: int) -> np.ndarray:
    if audio is None or audio.n == 0:
        return np.zeros(n, dtype=np.float32)
    jump = _fit(audio.jump, n)
    flux = _fit(audio.flux, n)
    laugh = _fit(audio.laughter, n)
    energy = _fit(audio.energy, n)
    raw = 0.42 * jump + 0.20 * np.clip(flux, 0, 1) + 0.24 * laugh + 0.14 * energy
    return np.clip(raw, 0.0, 1.0).astype(np.float32)


def _visual_channel(visual: Optional[VisualFeatures], n: int, hop: float) -> np.ndarray:
    if visual is None or visual.n == 0 or not visual.analyzed:
        return np.zeros(n, dtype=np.float32)
    step = 1.0 / max(1e-6, visual.fps)
    src_t = np.arange(visual.n, dtype=np.float32) * step
    dst_t = np.arange(n, dtype=np.float32) * hop

    def _resample(arr: np.ndarray) -> np.ndarray:
        if arr.size == 0:
            return np.zeros(n, dtype=np.float32)
        return np.interp(dst_t, src_t[:arr.size], arr).astype(np.float32)

    scene = _resample(visual.scene)
    motion = _resample(visual.motion)
    flash = _resample(visual.flash)
    raw = 0.45 * np.clip(scene, 0, 1) + 0.35 * np.clip(motion, 0, 1) + 0.20 * np.clip(flash, 0, 1)
    return np.clip(raw, 0.0, 1.0).astype(np.float32)


def _speech_channel(transcript: Optional[TranscriptResult], n: int,
                    hop: float, *, language: Optional[str] = None
                    ) -> tuple[np.ndarray, np.ndarray]:
    """עניין לשוני + מסכה של היכן יש דיבור."""
    out = np.zeros(n, dtype=np.float32)
    mask = np.zeros(n, dtype=bool)
    if transcript is None or not transcript.segments:
        return out, mask

    rates = [s.words_per_second for s in transcript.segments if s.duration > 0.4]
    median_rate = float(np.median(rates)) if rates else 2.5

    for seg in transcript.segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        i0 = int(max(0, min(n - 1, round(seg.start / hop))))
        i1 = int(max(i0 + 1, min(n, round(seg.end / hop))))
        mask[i0:i1] = True

        lex = lexical_score(text, language=seg.language or language)
        punct = 0.0
        if "?" in text:
            punct += 0.25
        if "!" in text:
            punct += 0.30
        exclam = min(0.2, 0.08 * text.count("!"))

        # קצב דיבור מהיר יחסית לחציון = התלהבות
        rate = seg.words_per_second
        rate_boost = 0.0
        if median_rate > 0:
            rate_boost = float(np.clip((rate / median_rate - 1.0) * 0.6, -0.15, 0.35))

        # ביטחון התמלול – מקטעים עם ביטחון נמוך שוקלים פחות
        conf = float(np.clip(1.0 + seg.avg_logprob / 1.2, 0.25, 1.0))
        speech_conf = float(np.clip(1.0 - seg.no_speech_prob, 0.2, 1.0))

        value = np.clip((0.62 * lex + punct + exclam + rate_boost) * conf * speech_conf,
                        0.0, 1.0)
        out[i0:i1] = np.maximum(out[i0:i1], value)

    return out.astype(np.float32), mask


def lexical_score(text: str, language: Optional[str] = None) -> float:
    """
    ציון 0..1 לפי מילות מפתח, בגבולות מילה.

    באג שתוקן: ההתאמה הייתה על תת-מחרוזת, ולכן „מת" נמצא בתוך „אמת"
    ו-„lol" בתוך „lollipop". עכשיו כל ביטוי נבדק בגבולות מילה (עם
    תחיליות עבריות), והתקרה של שש התאמות נאכפת על כל הלקסיקונים יחד.

    `language` בוחר את חבילת השפה; None = כל החבילות.
    """
    if not text:
        return 0.0
    total = 0.0
    hits = 0
    for pack in _lang.packs_for(language):
        for phrase in pack.matched(text, list(pack.lexicon)):
            total += pack.lexicon[phrase]
            hits += 1
            if hits >= MAX_LEXICAL_HITS:
                break
        if hits >= MAX_LEXICAL_HITS:
            break
    if hits == 0:
        return 0.0
    # רוויה: 1 התאמה חזקה ≈ 0.55, 3 התאמות ≈ 0.85
    return float(min(1.0, 1.0 - math.exp(-0.65 * total)))


def classify_text(text: str, language: Optional[str] = None) -> tuple[str, str]:
    """
    מחזיר (קטגוריה, תווית בשפת הממשק) לפי ביטויים בגבולות מילה.
    ברירת מחדל: רגע בולט.
    """
    from .. import i18n

    if text:
        packs = _lang.packs_for(language)
        order = [key for key, _ in packs[0].category_patterns] if packs else []
        for key in order:
            for pack in packs:
                phrases = dict(pack.category_patterns).get(key, ())
                if phrases and pack.count(text, phrases):
                    return key, i18n.tr(f"analysis.category.{key}")
    return "moment", i18n.tr("analysis.category.moment")


def _pause_channel(audio: Optional[AudioFeatures], transcript: Optional[TranscriptResult],
                   n: int, hop: float) -> np.ndarray:
    """
    שקט דרמטי → דיבור. מזהה מעבר משקט של 0.8-6 שניות לדיבור,
    ונותן ציון לחלון שאחרי השקט. זה מה שמאתר "רגעים שקטים מעניינים".
    """
    out = np.zeros(n, dtype=np.float32)
    if audio is None or audio.n == 0:
        return out

    silence = _fit_bool(audio.silence, n)
    min_len = max(1, int(round(0.8 / hop)))
    max_len = max(min_len + 1, int(round(6.0 / hop)))
    after = max(1, int(round(4.0 / hop)))

    i = 0
    while i < n:
        if not silence[i]:
            i += 1
            continue
        j = i
        while j < n and silence[j]:
            j += 1
        length = j - i
        if min_len <= length <= max_len and j < n:
            # עוצמת האפקט גדלה עם אורך השקט
            strength = float(np.clip(length / max_len, 0.25, 1.0))
            end = min(n, j + after)
            ramp = np.linspace(strength, strength * 0.35, end - j, dtype=np.float32)
            out[j:end] = np.maximum(out[j:end], ramp)
        i = max(j, i + 1)

    return out


def _chat_channel(events: Optional[list[tuple[float, float]]], n: int,
                  hop: float) -> np.ndarray:
    """
    אות מפעילות צ'אט: רשימת (זמן, עוצמה). מיושם רק כאשר המשתמש
    הפעיל זאת ויש גישה מורשית לנתוני הצ'אט.
    """
    out = np.zeros(n, dtype=np.float32)
    if not events:
        return out
    for t, weight in events:
        i = int(max(0, min(n - 1, round(t / hop))))
        out[i] = max(out[i], float(np.clip(weight, 0.0, 1.0)))
    return _smooth(out, sigma_frames=max(2, int(round(3.0 / hop))))


# --------------------------------------------------------------------------
# היתוך
# --------------------------------------------------------------------------
def fuse_score(tl: Timeline, settings: AppSettings) -> Timeline:
    """
    מחשב מחדש את הציון המשולב מתוך הערוצים השמורים.

    הערוצים נשמרים בשלב הניתוח; הציון תלוי בהגדרות (רגישות), ולכן
    יצירה חוזרת עם הגדרות אחרות מחשבת אותו מחדש בלי לנתח שוב.
    """
    tl.score = _fuse(tl, settings)
    return tl


def _fuse(tl: Timeline, settings: AppSettings) -> np.ndarray:
    n = tl.n
    if n == 0:
        return np.zeros(0, dtype=np.float32)

    channels = np.vstack([tl.vocal, tl.speech, tl.visual, tl.pause, tl.chat])
    weights = np.array([0.34, 0.30, 0.18, 0.12, 0.06], dtype=np.float32)

    # ערוצים ריקים (למשל אין תמלול) – מחלקים מחדש את המשקל
    active = np.array([float(c.max() > 1e-6) for c in channels], dtype=np.float32)
    if active.sum() == 0:
        return np.zeros(n, dtype=np.float32)
    w = weights * active
    w = w / w.sum()

    weighted = (channels * w[:, None]).sum(axis=0)
    strongest = channels.max(axis=0)

    # 60% שקלול + 40% הערוץ החזק: רגע חזק בערוץ אחד בלבד עדיין בולט
    fused = 0.60 * weighted + 0.40 * strongest

    # החלקה: מחפשים רגעים, לא פיקים של פריים בודד
    sigma = max(1, int(round(1.4 / max(1e-6, tl.hop))))
    fused = _smooth(fused.astype(np.float32), sigma_frames=sigma)

    # נרמול לטווח 0..1 מול אחוזון גבוה (עמיד לחריגים)
    ref = float(np.percentile(fused, 99.0))
    if ref > 1e-6:
        fused = np.clip(fused / ref, 0.0, 1.0)

    # רגישות: גמא. רגישות גבוהה מרימה רגעים בינוניים
    sens = float(np.clip(settings.sensitivity, 0.0, 1.0))
    gamma = 1.9 - 1.3 * sens      # 1.9 (סלקטיבי) .. 0.6 (מכליל)
    return np.power(fused, gamma).astype(np.float32)


# --------------------------------------------------------------------------
# עזרים
# --------------------------------------------------------------------------
def _fit(arr: np.ndarray, n: int) -> np.ndarray:
    """מתאים אורך מערך ל-n על-ידי חיתוך או ריפוד בערך האחרון."""
    a = np.asarray(arr, dtype=np.float32)
    if a.size == n:
        return a
    if a.size == 0:
        return np.zeros(n, dtype=np.float32)
    if a.size > n:
        return a[:n]
    pad = np.full(n - a.size, a[-1], dtype=np.float32)
    return np.concatenate([a, pad])


def _fit_bool(arr: np.ndarray, n: int) -> np.ndarray:
    a = np.asarray(arr, dtype=bool)
    if a.size == n:
        return a
    if a.size == 0:
        return np.zeros(n, dtype=bool)
    if a.size > n:
        return a[:n]
    return np.concatenate([a, np.full(n - a.size, a[-1], dtype=bool)])


def _smooth(x: np.ndarray, sigma_frames: int) -> np.ndarray:
    """החלקה גאוסיאנית עם גרעין סופי (ללא scipy – תלות אחת פחות)."""
    if x.size == 0 or sigma_frames <= 0:
        return x
    radius = max(1, int(3 * sigma_frames))
    t = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-(t ** 2) / (2.0 * sigma_frames ** 2))
    kernel /= kernel.sum()
    padded = np.pad(x, radius, mode="edge")
    return np.convolve(padded, kernel, mode="valid").astype(np.float32)
