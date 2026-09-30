"""
קריאת WAV PCM ישירות ל-numpy, בלי PyAV.

faster-whisper מפענח קבצים דרך PyAV. בגרסה 1.2.1 הוא קורא ל-
`av.open(..., metadata_errors="ignore")`, ו-PyAV 19 הסיר את הפרמטר הזה –
התמלול נופל עם TypeError. הגרסה הנעולה ב-requirements.txt (av==18.1.0)
פותרת את זה, והקריאה הישירה כאן היא שכבת הגנה נוספת: הפייפליין כבר
מחלץ WAV 16kHz מונו, אז מעבירים ל-Whisper מערך מוכן ו-PyAV לא נוגע בו.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Optional

import numpy as np


def read_wav_float32(path: str | Path, *, start: float = 0.0,
                     duration: Optional[float] = None,
                     expect_rate: Optional[int] = 16000) -> Optional[np.ndarray]:
    """
    מחזיר את האות כ-float32 בטווח [-1, 1], או None אם הקובץ אינו
    PCM 16-bit מונו בקצב הצפוי (ואז הקורא יחזור לפענוח הרגיל).
    `start`/`duration` בשניות מאפשרים לקרוא חלון קצר בלי לטעון הכול.
    """
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                return None
            rate = w.getframerate()
            if expect_rate and rate != expect_rate:
                return None
            total = w.getnframes()
            first = max(0, min(total, int(round(start * rate))))
            count = total - first if duration is None else max(0, int(round(duration * rate)))
            count = min(count, total - first)
            w.setpos(first)
            raw = w.readframes(count)
    except (wave.Error, EOFError, OSError):
        return None
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def wav_duration(path: str | Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError, OSError):
        return 0.0
