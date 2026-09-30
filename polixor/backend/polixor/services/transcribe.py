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

import numpy as np

from .. import i18n
from ..config import PATHS, AppSettings
from ..errors import (
    JobCancelledError,
    ModelUnavailableError,
    TranscriptionError,
)
from ..util.text import detect_language_hint
from ..util.wav import read_wav_float32, rms_envelope, wav_duration

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
    # איך התמלול הופק: פרופיל, מודל, beam, שפה וביטחון בזיהויה, אוצר מילים,
    # מקטעים – לשחזור ולדוח האיכות
    meta: dict[str, Any] = field(default_factory=dict)

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
                   media_duration: float = 0.0, **kw: Any) -> TranscriptResult:
        raise NotImplementedError


class NullProvider(TranscriptProvider):
    """ללא תמלול – מצב ניתוח אודיו/וידאו בלבד."""

    name = "none"

    def transcribe(self, audio_path: Path, *, settings: AppSettings,
                   on_progress: ProgressFn = None,
                   cancel_event: Optional[threading.Event] = None,
                   media_duration: float = 0.0, **kw: Any) -> TranscriptResult:
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
                   media_duration: float = 0.0, **kw: Any) -> TranscriptResult:
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
                message_key="processing.transcribe.fixture_missing",
                hint_key="processing.transcribe.fixture_hint",
                params={"name": candidates[0].name},
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
    """
    תמלול מקומי עם faster-whisper (CTranslate2).

    * התצורה (מודל, beam, אצווה, שפה, אוצר מילים) נקבעת ב-profiles.asr_plan
    * שפה "אוטומטית" מזוהה מכמה דגימות לאורך המקור ואז ננעלת לכל התמלול
    * אוצר המילים עובר כ-hotwords – הטיה בלבד, בלי החלפת מילים
    * אודיו ארוך מתומלל במקטעים שנחתכים בנקודות שקטות; כל מקטע נשמר
      (checkpoint), כך שתמלול של שעות שנקטע ממשיך מאיפה שעצר
    """

    name = "faster-whisper"
    _model_cache: dict[tuple[str, str, str], Any] = {}
    _cache_lock = threading.Lock()

    def _load_model(self, settings: AppSettings, plan: Any = None):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ModelUnavailableError(
                message_key="processing.transcribe.no_whisper",
                hint_key="processing.transcribe.no_whisper_hint",
                detail=str(exc),
            ) from exc

        if plan is None:
            from ..profiles import asr_plan

            plan = asr_plan(settings)
        key = (plan.model, plan.device, plan.compute_type)
        with self._cache_lock:
            if key in self._model_cache:
                return self._model_cache[key]

        PATHS.models.mkdir(parents=True, exist_ok=True)
        try:
            model = WhisperModel(
                plan.model,
                device=plan.device,
                compute_type=plan.compute_type,
                download_root=str(PATHS.models),
            )
        except Exception as exc:
            msg = str(exc)
            low = msg.lower()
            if any(k in low for k in ("connect", "proxy", "resolve", "network",
                                      "403", "timeout", "ssl", "offline", "repository not found",
                                      "404")):
                raise ModelUnavailableError(
                    message_key="processing.transcribe.download_failed",
                    hint_key="processing.transcribe.download_hint",
                    params={"model": plan.model, "path": PATHS.models},
                    detail=msg,
                ) from exc
            if "out of memory" in low or "cuda" in low:
                raise TranscriptionError(
                    message_key="processing.transcribe.gpu_memory",
                    hint_key="processing.transcribe.gpu_memory_hint",
                    detail=msg,
                ) from exc
            raise TranscriptionError(message_key="processing.transcribe.load_failed",
                                     detail=msg) from exc

        with self._cache_lock:
            self._model_cache[key] = model
        log.info("whisper model loaded: %s on %s (%s)", *key)
        return model

    def transcribe(self, audio_path: Path, *, settings: AppSettings,
                   on_progress: ProgressFn = None,
                   cancel_event: Optional[threading.Event] = None,
                   media_duration: float = 0.0,
                   checkpoint_dir: Optional[Path] = None, **kw: Any) -> TranscriptResult:
        from ..profiles import asr_plan

        plan = asr_plan(settings)
        model = self._load_model(settings, plan)
        total = wav_duration(audio_path) or float(media_duration or 0.0)
        env = rms_envelope(audio_path)
        chunks = plan_chunks(env, total) if env is not None else [(0.0, total)]

        # ---- שפה: דגימות מכל המקור, ואז נעילה ----
        language, lang_prob = plan.language, None
        if language is None:
            language, lang_prob = _detect_language(model, audio_path, total, env)

        runner = model
        if plan.batched:
            try:
                from faster_whisper import BatchedInferencePipeline

                runner = BatchedInferencePipeline(model=model)
            except Exception as exc:                    # noqa: BLE001
                log.warning("batched pipeline unavailable: %s", exc)
                runner = model
        batched = runner is not model

        cfg_key = _config_key(plan, language, audio_path, chunks)
        out: list[Segment] = []
        for ci, (c0, c1) in enumerate(chunks):
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelledError()
            cached = _load_chunk(checkpoint_dir, ci, cfg_key)
            if cached is not None:
                out.extend(cached)
                if on_progress and total > 0:
                    on_progress(min(0.99, c1 / total), i18n.tr(
                        "pipeline.transcribe.progress", time=_mmss(c1)))
                continue
            segs = self._run_chunk(runner, batched, plan, language, audio_path, c0, c1, total,
                                   on_progress, cancel_event, have_output=bool(out))
            _save_chunk(checkpoint_dir, ci, cfg_key, segs)
            out.extend(segs)

        if on_progress:
            on_progress(1.0, i18n.tr("pipeline.transcribe.finished", n=len(out)))

        detected = language or ""
        if not detected and out:
            detected = detect_language_hint(" ".join(s.text for s in out[:20]))
        for sgm in out:
            sgm.language = sgm.language or detected
        meta = {**plan.to_dict(), "language": detected, "batched_used": batched,
                "language_probability": lang_prob, "chunks": len(chunks),
                "vocabulary_terms": len(getattr(settings, "asr_vocabulary", []) or [])}
        note = ""
        if lang_prob is not None and lang_prob < 0.5:
            note = i18n.tr("pipeline.transcribe.language_uncertain", language=detected,
                           pct=f"{lang_prob * 100:.0f}")
        return TranscriptResult(
            segments=out, language=detected, duration=total or media_duration,
            provider=self.name, model=plan.model, note=note, meta=meta,
        )

    def _run_chunk(self, runner: Any, batched: bool, plan: Any, language: Optional[str],
                   audio_path: Path, c0: float, c1: float, total: float,
                   on_progress: ProgressFn, cancel_event: Optional[threading.Event],
                   have_output: bool) -> list[Segment]:
        audio = read_wav_float32(audio_path, start=c0, duration=c1 - c0)
        offset = c0
        if audio is None:
            audio, offset = whisper_audio_input(audio_path), 0.0
        kwargs: dict[str, Any] = dict(
            language=language, task="transcribe", beam_size=plan.beam_size,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
            word_timestamps=True,
            condition_on_previous_text=False,   # מפחית לולאות חזרה בשידורים ארוכים
            hotwords=plan.hotwords,
        )
        if batched:
            kwargs["batch_size"] = plan.batch_size
        try:
            segments_iter, info = runner.transcribe(audio, **kwargs)
        except Exception as exc:
            raise TranscriptionError(message_key="processing.transcribe.failed",
                                     detail=str(exc)) from exc
        out: list[Segment] = []
        try:
            for seg in segments_iter:
                if cancel_event is not None and cancel_event.is_set():
                    raise JobCancelledError()
                words = [
                    Word(start=float(w.start) + offset, end=float(w.end) + offset,
                         text=str(w.word).strip(),
                         probability=float(getattr(w, "probability", 1.0) or 1.0))
                    for w in (getattr(seg, "words", None) or [])
                    if w.start is not None and w.end is not None
                ]
                out.append(Segment(
                    start=float(seg.start) + offset, end=float(seg.end) + offset,
                    text=(seg.text or "").strip(), words=words,
                    language=getattr(info, "language", "") or (language or ""),
                    avg_logprob=float(getattr(seg, "avg_logprob", 0.0) or 0.0),
                    no_speech_prob=float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
                ))
                if on_progress and total > 0:
                    t = float(seg.end) + offset
                    on_progress(min(0.99, t / total), i18n.tr(
                        "pipeline.transcribe.progress", time=_mmss(t)))
        except JobCancelledError:
            raise
        except Exception as exc:
            if out or have_output:
                log.warning("transcription chunk %.0f-%.0f stopped early: %s", c0, c1, exc)
            else:
                raise TranscriptionError(message_key="processing.transcribe.failed_midway",
                                         detail=str(exc)) from exc
        return out


# --------------------------------------------------------------------------
# מקטעים, זיהוי שפה ונקודות שמירה
# --------------------------------------------------------------------------
# מתחת לאורך הזה – קריאה אחת (כמו תמיד)
CHUNK_MIN_TOTAL = 1200.0
# אורך מקטע יעד, ורוחב החיפוש של נקודה שקטה סביב כל גבול
CHUNK_TARGET = 600.0
CHUNK_SEARCH = 30.0
ENVELOPE_HOP = 0.1


def plan_chunks(env: Optional[np.ndarray], total: float, *, hop: float = ENVELOPE_HOP,
                target: float = CHUNK_TARGET, search: float = CHUNK_SEARCH
                ) -> list[tuple[float, float]]:
    """
    גבולות מקטעים לתמלול: כל ~10 דקות, בנקודה השקטה ביותר (חלון של חצי
    שנייה) בטווח ±30 שניות – כדי שאף מילה לא תיחתך בין מקטעים.
    """
    if total <= CHUNK_MIN_TOTAL or env is None or env.size == 0:
        return [(0.0, total)]
    win = max(1, int(round(0.5 / hop)))
    smooth = np.convolve(env, np.ones(win, dtype=np.float32) / win, mode="same")
    speech_level = float(np.median(smooth)) or 1e-6
    cuts = [0.0]
    t = target
    while t < total - target * 0.5:
        cut = t
        # אם אין הפסקה בטווח (דיבור רציף) – מרחיבים את החיפוש פעמיים
        for width in (search, search * 2, search * 4):
            i0 = max(0, int((t - width) / hop))
            i1 = min(smooth.size, int((t + width) / hop))
            if i1 <= i0:
                break
            k = i0 + int(np.argmin(smooth[i0:i1]))
            cut = k * hop
            if float(smooth[k]) <= 0.3 * speech_level:
                break
        if cut - cuts[-1] > target * 0.4:
            cuts.append(round(cut, 2))
        t = cut + target
    cuts.append(total)
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a > 0.05]


def _detect_language(model: Any, audio_path: Path, total: float,
                     env: Optional[np.ndarray]) -> tuple[Optional[str], Optional[float]]:
    """
    זיהוי שפה מכמה דגימות של 30 שניות לאורך המקור (לא רק מההתחלה, שבה
    לפעמים יש מוזיקה או ברכה באנגלית). None אם לא ניתן לזהות.
    """
    try:
        points = [0.15, 0.4, 0.65, 0.9] if total > 240 else [0.0]
        parts = []
        for f in points:
            start = max(0.0, min(total - 30.0, total * f))
            a = read_wav_float32(audio_path, start=start, duration=30.0)
            if a is not None and a.size:
                parts.append(a)
        if not parts:
            return None, None
        audio = np.concatenate(parts)
        lang, prob, _ = model.detect_language(audio=audio, vad_filter=True,
                                              language_detection_segments=len(parts))
        return (lang or None), (float(prob) if prob is not None else None)
    except Exception as exc:                            # noqa: BLE001
        log.warning("language detection failed: %s", exc)
        return None, None


def _config_key(plan: Any, language: Optional[str], audio_path: Path,
                chunks: list[tuple[float, float]]) -> str:
    import hashlib

    try:
        st = Path(audio_path).stat()
        fp = f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        fp = ""
    raw = json.dumps({"model": plan.model, "beam": plan.beam_size, "batched": plan.batched,
                      "hotwords": plan.hotwords, "language": language, "audio": fp,
                      "chunks": chunks}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _chunk_path(checkpoint_dir: Optional[Path], index: int) -> Optional[Path]:
    return (Path(checkpoint_dir) / f"part-{index:04d}.json") if checkpoint_dir else None


def segments_to_json(segs: list[Segment]) -> list[dict[str, Any]]:
    return [{"start": s.start, "end": s.end, "text": s.text, "language": s.language,
             "avg_logprob": s.avg_logprob, "no_speech_prob": s.no_speech_prob,
             "words": [w.to_dict() for w in s.words]} for s in segs]


def segments_from_json(items: list[dict[str, Any]]) -> list[Segment]:
    return [Segment(start=float(s["start"]), end=float(s["end"]), text=s.get("text", ""),
                    language=s.get("language", ""),
                    avg_logprob=float(s.get("avg_logprob", 0.0)),
                    no_speech_prob=float(s.get("no_speech_prob", 0.0)),
                    words=[Word(start=float(w["start"]), end=float(w["end"]),
                                text=w.get("text", ""), probability=float(w.get("p", 1.0)))
                           for w in s.get("words", [])])
            for s in items]


def _save_chunk(checkpoint_dir: Optional[Path], index: int, key: str,
                segs: list[Segment]) -> None:
    path = _chunk_path(checkpoint_dir, index)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": key, "segments": segments_to_json(segs)},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_chunk(checkpoint_dir: Optional[Path], index: int, key: str) -> Optional[list[Segment]]:
    path = _chunk_path(checkpoint_dir, index)
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("key") != key:
        return None
    return segments_from_json(data.get("segments") or [])


def _mmss(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def whisper_audio_input(audio_path: Path) -> Any:
    """
    WAV שהפייפליין חילץ נקרא ישירות למערך – כך PyAV לא משתתף בפענוח
    (ראו util/wav.py). קובץ בפורמט אחר עובר כנתיב, בדרך הרגילה.
    """
    arr = read_wav_float32(audio_path)
    return str(audio_path) if arr is None else arr


def pyav_status() -> dict[str, Any]:
    """
    גרסאות faster-whisper ו-PyAV והאם הצירוף ידוע כשבור
    (faster-whisper 1.2.x קורא ל-av.open עם metadata_errors, ש-PyAV 19 הסיר).
    """
    from importlib import metadata

    def _ver(name: str) -> str:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return ""

    fw, av = _ver("faster-whisper"), _ver("av")
    try:
        av_major = int(av.split(".")[0]) if av else 0
    except ValueError:
        av_major = 0
    return {"faster_whisper": fw, "av": av,
            "compatible": not (fw and av_major >= 19)}


def _cuda_available() -> bool:
    from ..profiles import cuda_available

    return cuda_available()


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
    checkpoint_dir: Optional[Path] = None,
) -> TranscriptResult:
    """
    מתמלל, ואם המודל אינו זמין – ממשיך במצב ללא תמלול במקום להפיל
    את כל המשימה (בהתאם ל-`allow_fallback`). `checkpoint_dir` – תיקייה
    לשמירת מקטעים שהסתיימו (המשך אחרי הפסקה).
    """
    provider = get_provider(settings.transcript_provider)
    try:
        return provider.transcribe(audio_path, settings=settings,
                                   on_progress=on_progress,
                                   cancel_event=cancel_event,
                                   media_duration=media_duration,
                                   checkpoint_dir=checkpoint_dir)
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
