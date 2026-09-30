"""
יישור כפוי (forced alignment) – אופציונלי, למצב איכות בלבד.

Whisper נותן זמני מילים מתוך מנגנון ה-attention, והם לא תמיד מדויקים
(במיוחד בעברית). יישור כפוי לוקח את הטקסט *הקיים* ומחפש בדיוק איפה כל
מילה נשמעת באודיו, בעזרת מודל CTC רב-לשוני (MMS_FA של torchaudio). הטקסט
לא משתנה – רק הזמנים.

התלות כבדה (torch + torchaudio + uroman, ומודל של ~1.2GB שיורד בשימוש
הראשון), ולכן היא לא חלק מההתקנה הרגילה:

    .venv/bin/pip install -r backend/requirements-alignment.txt

בלי ההתקנה – `status()` מחזיר לא זמין והכתוביות ממשיכות עם תיקון הזמנים
לפי אנרגיה (subtitle_align) בלבד. להשוואה על סרטון אמיתי לפני שמפעילים:
scripts/alignment_spike.py.

עברית: המודל עובד על אותיות לטיניות, ולכן כל מילה עוברת תעתיק (uroman):
„שלום" → "shlvm". מילה שאין לה תעתיק (מספר, סמל) שומרת את הזמנים שלה.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

log = logging.getLogger("polixor.forced_align")

SAMPLE_RATE = 16000
PAD = 0.25                  # אודיו סביב המשפט
MIN_WORD = 0.05

# (a, b, מילים) -> זמנים לכל מילה (None = לא יושרה), או None אם נכשל
Aligner = Callable[[float, float, Sequence[str]], Optional[list[Optional[tuple[float, float]]]]]

_lock = threading.Lock()
_backend: Any = None
_status: Optional[dict[str, Any]] = None


def status() -> dict[str, Any]:
    """האם הרכיבים מותקנים (בלי לטעון את המודל)."""
    global _status
    if _status is not None:
        return _status
    missing = []
    for mod in ("torch", "torchaudio", "uroman"):
        try:
            __import__(mod)
        except Exception:                              # noqa: BLE001
            missing.append(mod)
    if not missing:
        import torchaudio

        if not hasattr(getattr(torchaudio, "pipelines", None), "MMS_FA"):
            missing.append("torchaudio>=2.1")
    _status = {"available": not missing, "backend": "torchaudio-mms-fa", "missing": missing}
    return _status


_KEEP = re.compile(r"[^a-z']")


def romanize_words(words: Sequence[str], romanizer: Callable[[str], str]) -> list[str]:
    """תעתיק לאותיות שהמודל מכיר (a-z ו-'); מילה בלי תעתיק → מחרוזת ריקה."""
    out = []
    for w in words:
        try:
            r = romanizer(w) or ""
        except Exception:                              # noqa: BLE001
            r = ""
        out.append(_KEEP.sub("", r.lower()))
    return out


def spans_to_times(word_spans: Sequence[Sequence[Any]], frames: int, samples: int,
                   offset: float) -> list[tuple[float, float]]:
    """מספרי פריים של CTC → שניות (לפי היחס בין אורך האודיו למספר הפריימים)."""
    ratio = samples / max(1, frames) / SAMPLE_RATE
    out = []
    for spans in word_spans:
        s = offset + float(spans[0].start) * ratio
        e = offset + float(spans[-1].end) * ratio
        out.append((round(s, 3), round(max(e, s + MIN_WORD), 3)))
    return out


class _MmsBackend:
    """טעינה עצלה של המודל (פעם אחת לתהליך)."""

    def __init__(self) -> None:
        import torch
        import torchaudio
        import uroman

        self.torch = torch
        bundle = torchaudio.pipelines.MMS_FA
        self.model = bundle.get_model(with_star=False)
        self.model.eval()
        self.tokenizer = bundle.get_tokenizer()
        self.aligner = bundle.get_aligner()
        self._uroman = uroman.Uroman()

    def romanize(self, w: str) -> str:
        return self._uroman.romanize_string(w)

    def align(self, samples: Any, words: Sequence[str]) -> tuple[list[Any], int]:
        torch = self.torch
        wav = torch.from_numpy(samples).float().unsqueeze(0)
        with torch.inference_mode():
            emission, _ = self.model(wav)
        spans = self.aligner(emission[0], self.tokenizer(list(words)))
        return spans, int(emission.size(1))


def _get_backend() -> Any:
    global _backend
    with _lock:
        if _backend is None:
            _backend = _MmsBackend()
        return _backend


def make_aligner(audio_path: Path, *, backend: Any = None) -> Optional[Aligner]:
    """
    מחזיר פונקציית יישור לקובץ, או None כשהרכיבים לא מותקנים.
    `backend` – להזרקה בבדיקות (אותו ממשק כמו _MmsBackend).
    """
    if backend is None and not status()["available"]:
        return None

    def align(a: float, b: float, words: Sequence[str]
              ) -> Optional[list[Optional[tuple[float, float]]]]:
        from ..util.wav import read_wav_float32

        a0 = max(0.0, a - PAD)
        x = read_wav_float32(audio_path, start=a0, duration=(b + PAD) - a0)
        if x is None or x.size < SAMPLE_RATE // 10:
            return None
        try:
            be = backend or _get_backend()
            roman = romanize_words(words, be.romanize)
            keep = [i for i, r in enumerate(roman) if r]
            if not keep:
                return None
            spans, frames = be.align(x, [roman[i] for i in keep])
            times = spans_to_times(spans, frames, int(x.size), a0)
        except Exception as exc:                       # noqa: BLE001
            log.warning("forced alignment failed: %s", type(exc).__name__)
            return None
        out: list[Optional[tuple[float, float]]] = [None] * len(words)
        for i, t in zip(keep, times):
            out[i] = t
        return out

    return align
