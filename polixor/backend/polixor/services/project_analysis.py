"""
סיכום הניתוח לפרויקט (`ProjectAnalysis`) – מה שמסך Analysis מציג.

כל שדה כאן נמדד בפועל מתוצרי הניתוח. מה שלא ממומש מסומן בכנות:
זיהוי דוברים (diarization) אינו קיים בפרויקט, ולכן `speakers` תמיד
`available: false` עם הסבר – ולא מספר מומצא.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .. import i18n

log = logging.getLogger("polixor.project_analysis")


def build_analysis(ctx) -> dict[str, Any]:
    duration = float(ctx.source_info.get("duration") or 0.0)
    tr = ctx.transcript
    words = sum(len(s.words) or len((s.text or "").split())
                for s in (tr.segments if tr else []))
    transcript = {
        "available": bool(tr and tr.has_speech),
        "words": int(words),
        "segments": len(tr.segments) if tr else 0,
        "provider": (tr.provider if tr else "") or "",
        "note": (tr.note if tr and tr.note else None),
    }

    silence_seconds = sum(max(0.0, b - a) for a, b in (ctx.silences or []))
    speech_ratio = 0.0
    if ctx.timeline is not None and getattr(ctx.timeline, "speech_mask", None) is not None \
            and ctx.timeline.speech_mask.size:
        speech_ratio = float(ctx.timeline.speech_mask.mean())
    elif ctx.audio_feats is not None:
        speech_ratio = float(ctx.audio_feats.speech_ratio)
    loudness = None
    if ctx.audio_path is not None:
        try:
            from .audio import integrated_loudness

            value = integrated_loudness(ctx.audio_path)
            loudness = round(float(value), 1) if value is not None else None
        except Exception as exc:                      # noqa: BLE001
            log.debug("loudness failed: %s", exc)

    faces_seconds, faces_ratio = _faces(ctx.visual_feats)
    facecam = {"detected": False, "segments": 0, "box": None, "seconds": 0.0}
    screen = {"layouts": {"reaction": 0.0, "camera": 0.0, "screen": 0.0}}
    layout_note: Optional[str] = None
    lt = ctx.layout_timeline
    if lt is not None and lt.analyzed:
        summary = lt.summary()
        facecam = {"detected": bool(summary["facecam_detected"]),
                   "segments": int(summary["facecam_segments"]),
                   "box": summary["facecam_box"],
                   "seconds": float(summary["facecam_seconds"])}
        screen = {"layouts": dict(summary["seconds"]),
                  "segments": [s.to_dict() for s in lt.segments][:200]}
    elif lt is not None and lt.note:
        layout_note = lt.note

    return {
        "duration": round(duration, 2),
        "language": ctx.language or (tr.language if tr else None) or None,
        "transcript": transcript,
        "audio": {
            "available": ctx.audio_path is not None,
            "speech_ratio": round(speech_ratio, 3),
            "silence_seconds": round(silence_seconds, 1),
            "loudness_lufs": loudness,
        },
        "speakers": {
            "available": False, "count": None,
            "note": i18n.tr("analysis.speakers_unavailable"),
        },
        "faces": {"detected": faces_seconds > 0.0, "seconds": round(faces_seconds, 1),
                  "ratio": round(faces_ratio, 3)},
        "facecam": facecam,
        "screen": screen,
        "layout_note": layout_note,
        "moments": _moments_preview(ctx),
        "timeline_available": ctx.timeline is not None,
    }


def _faces(vf) -> tuple[float, float]:
    if vf is None or not vf.analyzed or not vf.faces:
        return 0.0, 0.0
    hits = sum(1 for frame in vf.faces if frame)
    step = 1.0 / max(1e-6, vf.fps)
    return hits * step, hits / max(1, len(vf.faces))


def _moments_preview(ctx, limit: int = 8) -> dict[str, Any]:
    """
    תצוגה מקדימה של רגעים מעניינים. **אינה** נשמרת כקליפים ואינה
    משפיעה על מה שייווצר: הבחירה בפועל נעשית בשלב היצירה לפי ההגדרות.
    """
    if ctx.timeline is None:
        return {"count": 0, "top": []}
    from . import selection

    try:
        from ..config import AppSettings

        probe = AppSettings.from_dict({**ctx.settings.to_dict(),
                                       "short_min_seconds": 15,
                                       "short_max_seconds": 45})
        cands = None
        # אותו מנוע שישמש ביצירה, כדי שהתצוגה המקדימה לא תבטיח יותר ממה שיבחר
        if probe.selection_engine == "intel":
            from . import clip_intel

            res = clip_intel.select_short_clips(ctx.timeline, ctx.transcript,
                                                settings=probe, language=ctx.language,
                                                limit=limit)
            cands = res.selected if res is not None else None
        if cands is None:
            boundaries = selection.BoundaryFinder(ctx.transcript, ctx.silences,
                                                  ctx.timeline.duration)
            cands = selection.build_short_candidates(
                ctx.timeline, transcript=ctx.transcript, boundaries=boundaries,
                settings=probe, limit=limit, language=ctx.language)
    except Exception as exc:                          # noqa: BLE001
        log.warning("moments preview failed: %s", exc)
        return {"count": 0, "top": []}
    top = sorted(cands, key=lambda c: c.score, reverse=True)[:limit]
    return {
        "count": len(cands),
        "top": [{"start": round(c.start, 2), "end": round(c.end, 2),
                 "score": round(c.score, 3), "title": c.title} for c in top],
    }
