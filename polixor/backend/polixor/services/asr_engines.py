"""
ASR engines for re-hearing parts of the audio: one interface, several sources.

    engine(a, b, prompt=None, hotwords=None) -> list[Segment] | None

  local   faster-whisper with any model (the strong Hebrew model, a second
          independent model, the fast model) on CPU or GPU – always available
          when the model can be loaded; nothing leaves the machine.
  cloud   the optional cloud recogniser (services/transcribe_cloud), in pieces
          of at most CLOUD_PIECE seconds. Never required: when it is not
          configured the local engines are used.

Every word an engine returns carries `asr=<engine label>` so later stages
know which hypothesis heard it.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional, Sequence

from ..config import AppSettings
from .transcribe import Segment, Word

log = logging.getLogger("polixor.asr_engines")

CLOUD_PIECE = 25.0
# one local decode at a time: parallel CPU decodes only slow each other down
_LOCAL_LOCK = threading.Lock()


class LocalEngine:
    def __init__(self, audio_path: Path, settings: AppSettings, language: Optional[str], *, model: str,
                 label: str, beam: int = 5, vad: bool = False, hotwords: Optional[str] = None,
                 cancel_event: Optional[threading.Event] = None) -> None:
        from ..profiles import asr_plan

        self.audio_path = Path(audio_path)
        self.settings = settings
        self.language = language
        base = asr_plan(settings, language=language)
        self.plan = type(base)(**{**base.to_dict(), "model": model, "beam_size": max(beam, 1)})
        self.model_name = model
        self.label = label
        self.vad = vad
        self.hotwords = hotwords if hotwords is not None else base.hotwords
        self.cancel_event = cancel_event
        self._model: Any = None
        self.status = ""

    @property
    def usable(self) -> bool:
        return self.status != "unavailable"

    def _load(self) -> Any:
        if self._model is None and self.usable:
            from .transcribe import FasterWhisperProvider

            try:
                with _LOCAL_LOCK:
                    self._model = FasterWhisperProvider()._load_model(self.settings, self.plan)
            except Exception as exc:                       # noqa: BLE001
                log.warning("ASR engine %s (%s) unavailable: %s", self.label, self.model_name, exc)
                self.status = "unavailable"
        return self._model

    def __call__(self, a: float, b: float, prompt: Optional[str] = None,
                 hotwords: Optional[str] = None) -> Optional[list[Segment]]:
        from ..util.wav import read_wav_float32

        if self.cancel_event is not None and self.cancel_event.is_set():
            return None
        model = self._load()
        if model is None:
            return None
        a = max(0.0, a)
        audio = read_wav_float32(self.audio_path, start=a, duration=max(0.3, b - a))
        if audio is None or audio.size == 0:
            return None
        try:
            with _LOCAL_LOCK:
                segs, info = model.transcribe(
                    audio, language=self.language or self.plan.language, task="transcribe",
                    beam_size=self.plan.beam_size, vad_filter=self.vad, word_timestamps=True,
                    condition_on_previous_text=False, initial_prompt=prompt or None,
                    hotwords=hotwords or self.hotwords)
                segs = list(segs)               # faster-whisper decodes lazily: decode inside the lock
            return [Segment(start=float(s.start) + a, end=float(s.end) + a, text=(s.text or "").strip(),
                            language=getattr(info, "language", "") or (self.language or ""),
                            words=[Word(float(w.start) + a, float(w.end) + a, str(w.word).strip(),
                                        float(getattr(w, "probability", 1.0) or 1.0), asr=self.label)
                                   for w in (s.words or []) if w.start is not None and w.end is not None])
                    for s in segs]
        except Exception as exc:                           # noqa: BLE001
            log.warning("ASR engine %s failed on %.1f-%.1f: %s", self.label, a, b, exc)
            return None


class CloudEngine:
    """The optional cloud recogniser, in short pieces (its windows are limited)."""

    def __init__(self, audio_path: Path, settings: AppSettings, language: Optional[str],
                 cancel_event: Optional[threading.Event] = None) -> None:
        from . import transcribe_cloud

        self.inner = transcribe_cloud.CloudRetranscriber(audio_path, settings, language, cancel_event)
        self.model_name = self.inner.model_name
        self.label = "cloud"
        self.status = ""

    @property
    def usable(self) -> bool:
        return self.status != "unavailable"

    def __call__(self, a: float, b: float, prompt: Optional[str] = None,
                 hotwords: Optional[str] = None) -> Optional[list[Segment]]:
        out: list[Segment] = []
        t = a
        while t < b - 0.2:
            u = min(b, t + CLOUD_PIECE)
            try:
                part = self.inner(t, u)
            except Exception as exc:                       # noqa: BLE001
                log.warning("cloud ASR failed on %.1f-%.1f: %s", t, u, exc)
                part = None
            if part is None:
                self.status = "unavailable" if not out else self.status
                return out or None
            for s in part:
                for w in s.words:
                    w.asr = "cloud"
            out += part
            t = u
        return out


def second_model(strong: str, language: Optional[str]) -> str:
    """An independent local model for the second hypothesis (a different model family than the strong one)."""
    from ..profiles import GENERAL_STRONG_MODEL

    return GENERAL_STRONG_MODEL if strong != GENERAL_STRONG_MODEL else "medium"


def words_of(segs: Optional[Sequence[Segment]]) -> list[Word]:
    return [w for s in segs or [] for w in s.words if (w.text or "").strip()]
