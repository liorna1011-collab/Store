"""
תמלול עם ספקים מתחלפים.

ספקים:
  faster-whisper – הספק האמיתי (ברירת מחדל). רץ מקומית, תומך עברית
                   ואנגלית, ומחזיר תזמון ברמת מילה.
  none           – ללא תמלול. הפייפליין ממשיך עם אותות אודיו/וידאו בלבד.
  fixture        – **כלי בדיקה בלבד**: קורא תמלול מוכן מקובץ JSON לצד
                   המדיה. נועד לבדיקות אוטומטיות של שאר השרשרת בסביבות
                   ללא גישה להורדת מודלים. אינו מבצע זיהוי דיבור,
                   ואינו מוצג למשתמש כיכולת AI.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .. import i18n
from ..config import PATHS, AppSettings
from ..errors import (
    JobCancelledError,
    ModelUnavailableError,
    TranscriptionError,
)
from ..util.text import detect_language_hint

log = logging.getLogger("polixor.transcribe")

ProgressFn = Optional[Callable[[float, str], None]]


@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3),
                "text": self.text, "p": round(self.probability, 3)}


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    language: str = ""
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def words_per_second(self) -> float:
        if self.duration <= 0:
            return 0.0
        count = len(self.words) or len(self.text.split())
        return count / self.duration


@dataclass
class TranscriptResult:
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0
    provider: str = ""
    model: str = ""
    note: str = ""          # הערה למשתמש (למשל: "תמלול לא בוצע")

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def has_speech(self) -> bool:
        return any(s.text.strip() for s in self.segments)

    def words_between(self, start: float, end: float) -> list[Word]:
        out: list[Word] = []
        for s in self.segments:
            if s.end < start or s.start > end:
                continue
            for w in (s.words or []):
                if w.end >= start and w.start <= end:
                    out.append(w)
        return out

    def text_between(self, start: float, end: float) -> str:
        parts = [s.text.strip() for s in self.segments
                 if s.end > start and s.start < end and s.text.strip()]
        return " ".join(parts)


# --------------------------------------------------------------------------
# ממשק הספק
# --------------------------------------------------------------------------
class TranscriptProvider:
    name = "base"

    def transcribe(self, audio_path: Path, *, settings: AppSettings,
                   on_progress: ProgressFn = None,
                   cancel_event: Optional[threading.Event] = None,
                   media_duration: float = 0.0) -> TranscriptResult:
        raise NotImplementedError


class NullProvider(TranscriptProvider):
    """ללא תמלול – מצב ניתוח אודיו/וידאו בלבד."""

    name = "none"

    def transcribe(self, audio_path: Path, *, settings: AppSettings,
                   on_progress: ProgressFn = None,
                   cancel_event: Optional[threading.Event] = None,
                   media_duration: float = 0.0) -> TranscriptResult:
        if on_progress:
            on_progress(1.0, i18n.tr("pipeline.transcribe.disabled"))
        return TranscriptResult(
            segments=[], language="", duration=media_duration, provider=self.name,
            note=i18n.tr("pipeline.transcribe.disabled_note"),
        )


class FixtureProvider(TranscriptProvider):
    """
    כלי בדיקה: טוען תמלול מקובץ `<audio>.transcript.json` או
    `<video>.transcript.json`. משמש אך ורק לבדיקות אוטומטיות.
    """

    name = "fixture"

    def transcribe(self, audio_path: Path, *, settings: AppSettings,
                   on_progress: ProgressFn = None,
                   cancel_event: Optional[threading.Event] = None,
                   media_duration: float = 0.0) -> TranscriptResult:
        import os

        candidates = []
        env_path = os.environ.get("POLIXOR_FIXTURE_TRANSCRIPT")
        if env_path:
            candidates.append(Path(env_path))
        candidates += [
            audio_path.with_suffix(".transcript.json"),
            audio_path.parent / "transcript.json",
        ]
        data = None
        for c in candidates:
            if c.exists():
                data = json.loads(c.read_text("utf-8"))
                break
        if data is None:
            raise TranscriptionError(
                "ספק הבדיקה לא מצא קובץ תמלול.",
                hint=f"צפוי: {candidates[0].name}",
            )

        segments: list[Segment] = []
        for item in data.get("segments", []):
            words = [
                Word(start=float(w["start"]), end=float(w["end"]),
                     text=str(w["text"]), probability=float(w.get("p", 1.0)))
                for w in item.get("words", [])
            ]
            segments.append(Segment(
                start=float(item["start"]), end=float(item["end"]),
                text=str(item.get("text", "")), words=words,
                language=item.get("language", data.get("language", "")),
                avg_logprob=float(item.get("avg_logprob", -0.2)),
                no_speech_prob=float(item.get("no_speech_prob", 0.05)),
            ))
        if on_progress:
            on_progress(1.0, i18n.tr("pipeline.transcribe.fixture_loaded"))
        return TranscriptResult(
            segments=segments,
            language=data.get("language", "") or (segments[0].language if segments else ""),
            duration=media_duration, provider=self.name, model="fixture",
            note=i18n.tr("pipeline.transcribe.fixture_note"),
        )


class FasterWhisperProvider(TranscriptProvider):
    """תמלול מקומי עם faster-whisper (CTranslate2)."""

    name = "faster-whisper"
    _model_cache: dict[tuple[str, str, str], Any] = {}
    _cache_lock = threading.Lock()

    def _load_model(self, settings: AppSettings):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ModelUnavailableError(
                "הספרייה faster-whisper אינה מותקנת.",
                hint="הרץ: pip install faster-whisper",
                detail=str(exc),
            ) from exc

        device = settings.whisper_device
        compute = settings.whisper_compute_type
        if device == "auto":
            device = "cuda" if _cuda_available() else "cpu"
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"

        key = (settings.whisper_model, device, compute)
        with self._cache_lock:
            if key in self._model_cache:
                return self._model_cache[key]

        PATHS.models.mkdir(parents=True, exist_ok=True)
        try:
            model = WhisperModel(
                settings.whisper_model,
                device=device,
                compute_type=compute,
                download_root=str(PATHS.models),
            )
        except Exception as exc:
            msg = str(exc)
            low = msg.lower()
            if any(k in low for k in ("connect", "proxy", "resolve", "network",
                                      "403", "timeout", "ssl", "offline")):
                raise ModelUnavailableError(
                    f"לא ניתן להוריד את מודל התמלול '{settings.whisper_model}'.",
                    hint="נדרשת גישה לאינטרנט בהורדה הראשונה. לאחר מכן המודל נשמר "
                         f"מקומית ב-{PATHS.models}. אפשר גם להעתיק ידנית תיקיית מודל לשם.",
                    detail=msg,
                ) from exc
            if "out of memory" in low or "cuda" in low:
                raise TranscriptionError(
                    "אין מספיק זיכרון GPU לטעינת המודל.",
                    hint="בחר מודל קטן יותר, או העבר את המכשיר ל-CPU בהגדרות.",
                    detail=msg,
                ) from exc
            raise TranscriptionError("טעינת מודל התמלול נכשלה.", detail=msg) from exc

        with self._cache_lock:
            self._model_cache[key] = model
        log.info("whisper model loaded: %s on %s (%s)", *key)
        return model

    def transcribe(self, audio_path: Path, *, settings: AppSettings,
                   on_progress: ProgressFn = None,
                   cancel_event: Optional[threading.Event] = None,
                   media_duration: float = 0.0) -> TranscriptResult:
        model = self._load_model(settings)

        language = None if settings.transcribe_language == "auto" else settings.transcribe_language

        try:
            segments_iter, info = model.transcribe(
                str(audio_path),
                language=language,
                task="transcribe",
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=True,
                condition_on_previous_text=False,   # מפחית לולאות חזרה בשידורים ארוכים
            )
        except Exception as exc:
            raise TranscriptionError("התמלול נכשל.", detail=str(exc)) from exc

        total = float(getattr(info, "duration", 0.0) or media_duration or 0.0)
        detected = getattr(info, "language", "") or ""
        out: list[Segment] = []

        try:
            for seg in segments_iter:
                if cancel_event is not None and cancel_event.is_set():
                    raise JobCancelledError()

                words = [
                    Word(start=float(w.start), end=float(w.end),
                         text=str(w.word).strip(),
                         probability=float(getattr(w, "probability", 1.0) or 1.0))
                    for w in (getattr(seg, "words", None) or [])
                    if w.start is not None and w.end is not None
                ]
                text = (seg.text or "").strip()
                out.append(Segment(
                    start=float(seg.start), end=float(seg.end), text=text, words=words,
                    language=detected,
                    avg_logprob=float(getattr(seg, "avg_logprob", 0.0) or 0.0),
                    no_speech_prob=float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
                ))
                if on_progress and total > 0:
                    frac = min(0.99, float(seg.end) / total)
                    on_progress(frac, i18n.tr(
                        "pipeline.transcribe.progress",
                        time=f"{int(seg.end // 60):02d}:{int(seg.end % 60):02d}"))
        except JobCancelledError:
            raise
        except Exception as exc:
            if out:
                log.warning("transcription stopped early: %s", exc)
            else:
                raise TranscriptionError("התמלול נכשל באמצע.", detail=str(exc)) from exc

        if on_progress:
            on_progress(1.0, i18n.tr("pipeline.transcribe.finished", n=len(out)))

        if not detected and out:
            detected = detect_language_hint(" ".join(s.text for s in out[:20]))

        return TranscriptResult(
            segments=out, language=detected, duration=total or media_duration,
            provider=self.name, model=settings.whisper_model,
        )


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# מפעל ספקים
# --------------------------------------------------------------------------
_PROVIDERS: dict[str, type[TranscriptProvider]] = {
    "faster-whisper": FasterWhisperProvider,
    "none": NullProvider,
    "fixture": FixtureProvider,
}


def get_provider(name: str) -> TranscriptProvider:
    cls = _PROVIDERS.get(name) or FasterWhisperProvider
    return cls()


def transcribe_audio(
    audio_path: Path,
    *,
    settings: AppSettings,
    on_progress: ProgressFn = None,
    cancel_event: Optional[threading.Event] = None,
    media_duration: float = 0.0,
    allow_fallback: bool = True,
) -> TranscriptResult:
    """
    מתמלל, ואם המודל אינו זמין – ממשיך במצב ללא תמלול במקום להפיל
    את כל המשימה (בהתאם ל-`allow_fallback`).
    """
    provider = get_provider(settings.transcript_provider)
    try:
        return provider.transcribe(audio_path, settings=settings,
                                   on_progress=on_progress,
                                   cancel_event=cancel_event,
                                   media_duration=media_duration)
    except JobCancelledError:
        raise
    except ModelUnavailableError as exc:
        if not allow_fallback:
            raise
        log.warning("transcription unavailable, continuing without it: %s", exc.message)
        result = NullProvider().transcribe(audio_path, settings=settings,
                                           media_duration=media_duration)
        result.note = i18n.tr("pipeline.transcribe.fallback_note",
                              message=exc.message, hint=exc.hint)
        return result
