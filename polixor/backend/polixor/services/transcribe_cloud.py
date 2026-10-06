"""
תמלול חוזר בענן – ראיה נוספת לקטעים לא בטוחים (מצב איכות בלבד, אם הוגדר).

רק החלון הקצר של המשפט החשוד נשלח (עד ~30 שניות), לא הסרטון. המפתח נשאר
בשרת (SECRETS) ולא נרשם ביומן. התוצאה היא *עוד ראיה מהאודיו*: שכבת ההגהה
(transcript_correct.decide) מקבלת תיקון רק כשהיא מסכימה עם מקור אחר או
כשהיא חזקה בבירור – הענן לא כותב טקסט לבד.

לפי התיעוד הרשמי של OpenAI (נבדק בזמן המימוש, 2026-09):
  * POST /v1/audio/transcriptions, multipart: file, model, language, prompt,
    response_format, include[];
  * gpt-4o-transcribe / gpt-4o-mini-transcribe: response_format json או text
    בלבד; `include[]=logprobs` מחזיר [{token, logprob, bytes}] (רק עם json);
  * זמני מילים (timestamp_granularities[]=word) קיימים רק ב-whisper-1 עם
    verbose_json – ולכן הזמנים כאן מוערכים לפי הדיבור בחלון, ומתוקנים
    אחר כך מול האודיו ב-subtitle_align.
"""

from __future__ import annotations

import io
import logging
import math
import threading
import wave
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import SECRETS, AppSettings
from .transcribe import Segment, Word

log = logging.getLogger("polixor.transcribe_cloud")

ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
MODELS = ("gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1")
DEFAULT_MODEL = "gpt-4o-transcribe"
TIMEOUT = 60.0


def available(settings: AppSettings) -> bool:
    """מופעל רק במצב איכות, רק כשהמשתמש ביקש, ורק כשיש מפתח בשרת."""
    from ..profiles import resolve_profile

    return (bool(getattr(settings, "asr_cloud_fallback", False))
            and resolve_profile(settings) == "quality"
            and bool(SECRETS.get("openai_api_key")))


def wav_bytes(samples: np.ndarray, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def word_probabilities(text: str, logprobs: Optional[list[dict[str, Any]]]) -> list[float]:
    """
    הסתברות לכל מילה (לפי רווחים) מתוך ה-logprobs של הטוקנים: הטוקנים
    מחוברים לפי הסדר, וכל מילה מקבלת את ההסתברות של הטוקן החלש שבה.
    בלי logprobs – ערך ניטרלי (0.7), כדי שהענן לא ייחשב "בטוח" בלי ראיה.
    """
    words = text.split()
    if not words:
        return []
    if not logprobs:
        return [0.7] * len(words)
    # מיקום כל מילה בטקסט
    spans, pos = [], 0
    for w in words:
        k = text.find(w, pos)
        spans.append((k, k + len(w)))
        pos = k + len(w)
    worst = [1.0] * len(words)
    cursor = 0
    for tk in logprobs:
        tok = str(tk.get("token", ""))
        try:
            p = math.exp(float(tk.get("logprob", 0.0)))
        except (TypeError, ValueError, OverflowError):
            p = 0.0
        a, b = cursor, cursor + len(tok)
        cursor = b
        for i, (s, e) in enumerate(spans):
            if a < e and b > s:
                worst[i] = min(worst[i], p)
    return [round(x, 3) for x in worst]


def estimate_word_times(words: list[str], a: float, b: float) -> list[tuple[float, float]]:
    """זמנים משוערים: פריסה על [a, b] לפי אורך המילים (subtitle_align מתקן אחר כך)."""
    if not words:
        return []
    weights = [max(1, len(w)) + 1 for w in words]
    total = float(sum(weights))
    out, t = [], a
    for w in weights:
        d = (b - a) * w / total
        out.append((round(t, 3), round(t + d, 3)))
        t += d
    return out


def to_segments(text: str, logprobs: Optional[list[dict[str, Any]]], a: float, b: float,
                language: str) -> list[Segment]:
    text = (text or "").strip()
    if not text:
        return []
    toks = text.split()
    probs = word_probabilities(text, logprobs)
    times = estimate_word_times(toks, a, b)
    words = [Word(start=s, end=e, text=t, probability=p)
             for t, p, (s, e) in zip(toks, probs, times)]
    return [Segment(start=a, end=b, text=text, words=words, language=language)]


class CloudRetranscriber:
    """אותו ממשק כמו WhisperRetranscriber: (a, b) -> משפטים, או None."""

    def __init__(self, audio_path: Path, settings: AppSettings, language: Optional[str],
                 cancel_event: Optional[threading.Event] = None, *, http: Any = None) -> None:
        self.audio_path = Path(audio_path)
        self.settings = settings
        self.language = language or ""
        self.cancel_event = cancel_event
        model = str(getattr(settings, "asr_cloud_model", DEFAULT_MODEL) or DEFAULT_MODEL)
        self.model_name = model if model in MODELS else DEFAULT_MODEL
        self._http = http
        self.calls = 0
        self.failures = 0
        self.seconds = 0.0

    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=TIMEOUT, trust_env=True)
        return self._http

    def _speech_bounds(self, x: np.ndarray, a: float, b: float) -> tuple[float, float]:
        from .subtitle_align import energy_from_samples

        e = energy_from_samples(x, a)
        if not e.valid:
            return a, b
        s = e.first_speech(a, b)
        t = e.last_speech(a, b)
        return (s if s is not None else a), (t if t is not None and t > (s or a) else b)

    def __call__(self, a: float, b: float) -> Optional[list[Segment]]:
        from .paid_guard import PaidAIDisabled, check

        try:
            check("openai:transcription")
        except PaidAIDisabled:
            return None                      # same as an unavailable cloud: the local result stands
        from ..util.wav import read_wav_float32
        from .vocabulary import hotwords

        if self.cancel_event is not None and self.cancel_event.is_set():
            return None
        key = SECRETS.get("openai_api_key")
        if not key:
            return None
        x = read_wav_float32(self.audio_path, start=a, duration=b - a)
        if x is None or x.size == 0:
            return None
        data: dict[str, Any] = {"model": self.model_name, "response_format": "json"}
        if self.model_name != "whisper-1":
            data["include[]"] = "logprobs"
        if self.language:
            data["language"] = self.language
        prompt = hotwords(getattr(self.settings, "asr_vocabulary", []) or [])
        if prompt:
            data["prompt"] = prompt[:800]
        self.calls += 1
        try:
            r = self._client().post(
                ENDPOINT, headers={"Authorization": f"Bearer {key}"}, data=data,
                files={"file": ("segment.wav", wav_bytes(x), "audio/wav")})
            if r.status_code >= 400:
                # רק קוד הסטטוס – לא גוף התשובה ולא המפתח
                log.warning("cloud re-transcription failed: HTTP %s", r.status_code)
                self.failures += 1
                return None
            body = r.json()
        except Exception as exc:                       # noqa: BLE001
            log.warning("cloud re-transcription failed: %s", type(exc).__name__)
            self.failures += 1
            return None
        self.seconds += b - a
        s, e = self._speech_bounds(x, a, b)
        return to_segments(str(body.get("text", "")), body.get("logprobs"), s, e, self.language)
