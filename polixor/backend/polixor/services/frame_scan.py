"""
פענוח משותף לניתוח החזותי ולזיהוי הפריסות.

שני הניתוחים דוגמים את אותו סרטון (חזותי: פריים לשנייה ב-320 פיקסל;
פריסות: פריים כל 2 שניות ב-960 פיקסל). קודם כל אחד פתח ffmpeg משלו
ופענח את *כל* הסרטון – והפענוח הוא רוב זמן הניתוח החזותי. כאן הסרטון
מפוענח פעם אחת, בקצב ובגודל שמתאימים לשניהם, וכל פריים מנותב למי
שצריך אותו.

המדידה על סרטון של 30 דקות (TESTING_GUIDE, "Performance"): הפענוח הנפרד
של הניתוח החזותי היה ~12 שניות מתוך ~21.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np

from ..errors import JobCancelledError
from .layout_detect import (LayoutTimeline, _analysis_size, detect_layouts,
                            detectors_available, iter_frames)
from .visual import VisualAccumulator, VisualFeatures, _load_face_cascade

log = logging.getLogger("polixor.frame_scan")

ProgressFn = Optional[Callable[[float], None]]

# מעל האורך הזה detect_layouts עובר לפריימי מפתח בלבד (זול בהרבה מפענוח מלא),
# ואז אין מה לשתף – כל ניתוח רץ בנפרד
KEYFRAME_LAYOUT_ABOVE = 45 * 60


def can_share(duration: float) -> bool:
    return 0 < duration <= KEYFRAME_LAYOUT_ABOVE and detectors_available()


def layout_interval(duration: float, sample_every: float = 2.0, max_samples: int = 1500) -> float:
    """אותו חישוב כמו ב-detect_layouts, כדי ששני הצדדים יסכימו על הרשת."""
    interval = max(0.25, float(sample_every))
    if duration / interval > max_samples:
        interval = duration / max_samples
    return interval


def visual_size(src_w: int, src_h: int, width: int = 320) -> tuple[int, int]:
    """אותו גודל כמו ב-analyze_video."""
    w = min(width, src_w)
    h = max(2, int(round(w * src_h / max(1, src_w))))
    return w, h - h % 2


def scan_visual_and_layouts(
    video_path: str | Path, *, duration: float, src_w: int, src_h: int,
    sample_fps: float = 1.0, layout_every: float = 2.0, detect_faces: bool = True,
    cancel_event: Optional[threading.Event] = None, on_progress: ProgressFn = None,
    decode: Optional[Callable[..., Iterator[tuple[float, np.ndarray]]]] = None,
) -> tuple[VisualFeatures, LayoutTimeline]:
    """
    ניתוח חזותי + זיהוי פריסות בפענוח אחד. `decode` – להזרקה בבדיקות
    (אותה חתימה כמו layout_detect.iter_frames).
    """
    import cv2

    path = Path(video_path)
    sample_fps = max(0.1, float(sample_fps))
    interval = layout_interval(duration, layout_every)
    rate = max(sample_fps, 1.0 / interval)
    lw, lh = _analysis_size(src_w, src_h, 960)
    vw, vh = visual_size(src_w, src_h)
    acc = VisualAccumulator(vw, vh, cascade=_load_face_cascade() if detect_faces else None)
    eps = 0.25 / rate
    counts = {"layout": 0, "decoded": 0}

    def frames() -> Iterator[tuple[float, np.ndarray]]:
        src = (decode or iter_frames)(path, width=lw, height=lh, interval=1.0 / rate,
                                      cancel_event=cancel_event)
        for t, frame in src:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelledError()
            counts["decoded"] += 1
            if t >= acc.count / sample_fps - eps:
                small = frame if (vw, vh) == (lw, lh) else \
                    cv2.resize(frame, (vw, vh), interpolation=cv2.INTER_AREA)
                acc.add(acc.count / sample_fps, small)
            if t >= counts["layout"] * interval - eps:
                counts["layout"] += 1
                yield t, frame
            if on_progress and duration > 0:
                on_progress(min(0.95, t / duration * 0.95))

    tl = detect_layouts(path, duration=duration, src_w=src_w, src_h=src_h,
                        sample_every=interval, keyframes_only=False, cancel_event=cancel_event,
                        frames=frames(), frame_size=(lw, lh))
    feats = acc.finish(fps=sample_fps, duration=duration)
    log.info("shared scan: %d decoded frames → %d visual, %d layout",
             counts["decoded"], acc.count, counts["layout"])
    if on_progress:
        on_progress(1.0)
    return feats, tl
