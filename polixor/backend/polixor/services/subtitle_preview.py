"""
תצוגה מקדימה של כתוביות – אותו כותב ASS ואותו libass של הרינדור הסופי.

הפריסה (שבירת שורות, גדלים, מיקום) מחושבת ברזולוציית היעד האמיתית,
והרסטר נעשה על קנבס קטן יותר: libass מקטין את מרחב הקואורדינטות של
ה-ASS (PlayResX/Y) לגודל הפריים, ולכן התמונה היא הקטנה נאמנה של מה
שייצרב בווידאו – לא הדמיה ב-CSS.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .. import i18n
from ..errors import FFmpegFailedError, FFmpegMissingError
from ..util.ffmpeg import ffmpeg_bin
from . import subtitle_render as sr
from .render import escape_filter_path
from .subtitle_style import clamp_style
from .subtitles import Cue

MAX_TEXT = 240
WORD_SECONDS = 0.42          # קצב דיבור לדוגמה (~140 מילים בדקה)
PLAIN_BACKGROUND = "0x2B3445"


@dataclass
class Preview:
    image: bytes
    media_type: str
    width: int                  # גודל התמונה שהוחזרה
    height: int
    target_width: int           # הרזולוציה שהפריסה חושבה לה
    target_height: int
    at: float                   # הזמן (בשניות) בכתובית הדוגמה
    lines: list[list[str]]      # השורות שמוצגות בפריים
    elapsed_ms: int


def sample_cue(text: str, *, language: str) -> Cue:
    """כתובית דוגמה עם תזמון מילים סינתטי וקבוע."""
    words, t = [], 0.0
    for tok in text.split():
        words.append({"start": round(t, 3), "end": round(t + WORD_SECONDS * 0.9, 3),
                      "text": tok})
        t += WORD_SECONDS
    return Cue(start=0.0, end=round(t, 3), text=text, words=words, language=language)


def _canvas(width: int, height: int, max_side: int) -> tuple[int, int]:
    scale = min(1.0, max_side / float(max(width, height)))
    w = max(2, int(round(width * scale / 2)) * 2)
    h = max(2, int(round(height * scale / 2)) * 2)
    return w, h


def render_preview(style: dict[str, Any], *, width: int, height: int,
                   language: str = "he", text: Optional[str] = None,
                   background: Optional[Path] = None, progress: float = 0.25,
                   max_side: int = 640, fmt: str = "png") -> Preview:
    """
    פריים אחד של הכתוביות בסגנון הנתון, מעל רקע (תמונה) או צבע אחיד.

    `progress` (0..1) הוא המיקום לאורך משפט הדוגמה – כך אפשר לראות את
    המילה הפעילה בקריוקי או את המילים שכבר הופיעו באנימציית word.
    """
    t0 = time.perf_counter()
    lang = "he" if (language or "").lower().startswith(("he", "iw")) else "en"
    st = clamp_style(style or {}, language=lang)
    sample = (text or "").strip() or i18n.tr("subtitles.sample", lang)
    sample = " ".join(sample.split())[:MAX_TEXT]
    cue = sample_cue(sample, language=lang)
    at = max(0.0, min(cue.end - 0.05, cue.end * max(0.0, min(1.0, float(progress)))))
    laid, _geo = sr.layout_cues([cue], st, width=width, height=height, language=lang)
    shown = next((lc for lc in laid if lc.start <= at < lc.end), laid[-1] if laid else None)
    out_w, out_h = _canvas(width, height, max_side)

    try:
        exe = ffmpeg_bin()
    except Exception as exc:                                  # noqa: BLE001
        raise FFmpegMissingError() from exc
    with tempfile.TemporaryDirectory(prefix="pxprev_") as d:
        ass = sr.write_ass_v2([cue], Path(d) / "p.ass", width=width, height=height,
                              style=st, language=lang)
        out = Path(d) / ("p.jpg" if fmt == "jpg" else "p.png")
        sub = f"ass='{escape_filter_path(ass)}'"
        if background is not None and Path(background).exists():
            # תמונת רקע היא פריים יחיד בזמן 0; מזיזים אותו לזמן `at` כדי
            # שה-ASS יוצג באותו רגע במשפט הדוגמה
            fit = (f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                   f"crop={out_w}:{out_h},setsar=1")
            cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(background),
                   "-vf", f"{fit},setpts=PTS+{at:.3f}/TB,{sub}", "-frames:v", "1"]
        else:
            cmd = [exe, "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i",
                   f"color=c={PLAIN_BACKGROUND}:s={out_w}x{out_h}:d={cue.end + 1:.2f}",
                   "-ss", f"{at:.3f}", "-vf", sub, "-frames:v", "1"]
        if fmt == "jpg":
            cmd += ["-q:v", "3"]
        cmd.append(str(out))
        res = subprocess.run(cmd, capture_output=True, timeout=60)
        if res.returncode != 0 or not out.exists():
            raise FFmpegFailedError(detail=res.stderr.decode("utf-8", "replace")[-400:])
        data = out.read_bytes()
    return Preview(image=data, media_type="image/jpeg" if fmt == "jpg" else "image/png",
                   width=out_w, height=out_h, target_width=width, target_height=height,
                   at=round(at, 3),
                   lines=[[ln.text for ln in shown.lines]] if shown else [],
                   elapsed_ms=int((time.perf_counter() - t0) * 1000))
