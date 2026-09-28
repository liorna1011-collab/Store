"""
גשר בין הבמאי לבין מנוע הרינדור.

הבמאי (`video_director`) מייצר **תכנית** — נתונים בלבד, בזמני המקור.
מנוע הרינדור מקבל `editing.EditPlan` עם ביטים בזמנים יחסיים לחלון
הקליפ. הקובץ הזה הוא ההמרה בין השניים, ורק היא.

למה קובץ נפרד: הבמאי לא יודע על FFmpeg, ומנוע הרינדור לא יודע על
סמנטיקה. שמירת ההמרה בנפרד מונעת מאחד מהם לזלוג לשני, ומשאירה את
הכלל „קודם תכנית מלאה, אחר כך ביצוע" נכון גם בקוד ולא רק בכוונה.

שתי נקודות עדינות:

  זמנים   הבמאי עובד בזמני השידור המלא. `Beat` עובד יחסית לתחילת
          החלון, כי FFmpeg כבר מקבל את החלון חתוך עם ‎-ss. ההפרש
          הוא `plan.source_start`, ומחסירים אותו פעם אחת, כאן.

  זום     החלטת זום עשויה לכסות רק חלק מקטע ששרד. לכן הקטע נחתך
          בגבולות הזום לתת-ביטים, במקום להחיל זום על קטע שלם
          שרובו לא אמור לזוז.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .. import i18n
from . import editing
from .video_director import Action, VideoEditPlan

log = logging.getLogger("polixor.director")

MIN_BEAT = 0.12     # ביט קצר מזה אינו נראה, ורק מוסיף תפר


def to_edit_plan(plan: VideoEditPlan, *,
                 style: Optional[str] = None) -> editing.EditPlan:
    """
    ממיר תכנית של הבמאי ל-`EditPlan` שהרנדרר יודע לבצע.

    מה שהתכנית לא כוללת — מוזיקה, מעברים, צבע — פשוט לא מופיע כאן.
    הגשר לא ממציא ערכים כדי „למלא" שדות.
    """
    segments = plan.to_segments()
    zooms = _zoom_spans(plan)
    base = plan.source_start

    beats: list[editing.Beat] = []
    for seg in segments:
        for s, e, zoom, why in _split_by_zoom(seg.source_start, seg.source_end,
                                              zooms):
            if e - s < MIN_BEAT:
                continue
            beats.append(editing.Beat(
                src_start=round(s - base, 3),
                src_end=round(e - base, 3),
                zoom=zoom,
                speed=seg.speed if seg.speed > 0 else 1.0,
                reason=why or seg.reason,
            ))

    beats = editing.cap_beats(beats)
    kept = sum(b.src_duration for b in beats)
    out = editing.EditPlan(
        beats=beats,
        style=style or (plan.pacing.profile.name if plan.pacing else "clean"),
        window_start=plan.source_start,
        window_end=plan.source_end,
        raw_duration=plan.source_duration,
        removed_seconds=round(max(0.0, plan.source_duration - kept), 3),
        # שתיקות שנשמרו בכוונה יושבות ברשימת החיתוכים כהחלטת
        # KEEP_PAUSE — הן חלק מאותה החלטה על אוויר מת.
        dramatic_kept=sum(1 for d in plan.cuts
                          if d.action == Action.KEEP_PAUSE.value),
        notes=list(plan.notes),
    )
    return out


def _zoom_spans(plan: VideoEditPlan) -> list[tuple[float, float, float, str]]:
    """החלטות מסגור פעילות, כטווחים ממוינים עם יעד הזום שלהם."""
    spans: list[tuple[float, float, float, str]] = []
    for d in plan.zoom_events:
        if not d.enabled:
            continue
        if d.action == Action.HOLD_FRAME.value:
            continue
        zoom = float(d.params.get("zoom", 1.0) or 1.0)
        if abs(zoom - 1.0) < 1e-3 or d.end <= d.start:
            continue
        spans.append((d.start, d.end, zoom, d.reason))
    spans.sort(key=lambda s: s[0])
    return spans


def _split_by_zoom(start: float, end: float,
                   zooms: list[tuple[float, float, float, str]]
                   ) -> list[tuple[float, float, float, str]]:
    """
    חותך קטע בגבולות החלטות הזום שחופפות לו.

    מחזיר רצף רציף שמכסה בדיוק את ‎[start, end]‎, כשכל חלק נושא את
    הזום שחל עליו (1.0 היכן שאין החלטה).
    """
    pieces: list[tuple[float, float, float, str]] = []
    cursor = start
    for z0, z1, zoom, why in zooms:
        if z1 <= cursor or z0 >= end:
            continue
        lo, hi = max(cursor, z0), min(end, z1)
        if lo > cursor + 1e-3:
            pieces.append((cursor, lo, 1.0, ""))
        if hi > lo:
            pieces.append((lo, hi, zoom, why))
        cursor = max(cursor, hi)
        if cursor >= end:
            break
    if cursor < end - 1e-3:
        pieces.append((cursor, end, 1.0, ""))
    return pieces or [(start, end, 1.0, "")]


def caption_preset_for(plan: VideoEditPlan, *,
                       fallback: str = "clean") -> str:
    """
    בוחר פריסט כתוביות שמתאים לקצב שהבמאי תכנן.

    הפריסט אינו מנוע נפרד אלא קבוצת פרמטרים, ולכן ההתאמה היא
    בין סגנון הקצב לבין מראה הכתובית — ולא החלפה של מנגנון.
    """
    if not plan.pacing:
        return fallback
    return {
        "viral_short": "viral",
        "clean_creator": "clean",
        "cinematic_story": "cinematic",
        "podcast_clip": "podcast",
        "educational": "clean",
        "product": "viral",
    }.get(plan.pacing.profile.name, fallback)


def explain(plan: VideoEditPlan) -> list[dict[str, Any]]:
    """
    „למה ה-AI עשה את זה?" — כל ההחלטות בשורה אחת כל אחת, מוכנות
    להצגה בממשק, כולל אלה שלא בוצעו ומדוע.
    """
    rows: list[dict[str, Any]] = []
    for group, items in (("cut", plan.cuts), ("frame", plan.zoom_events),
                         ("caption", plan.captions),
                         ("visual", plan.broll), ("pending", plan.pending)):
        for d in items:
            row = d.to_dict()
            # שתיקה שנשמרה אינה חיתוך, גם אם היא יושבת באותה רשימה
            row["group"] = ("pause"
                            if d.action == Action.KEEP_PAUSE.value else group)
            row["label"] = action_label(d.action)
            rows.append(row)
    rows.sort(key=lambda r: (r["start"], r["group"]))
    return rows


def action_label(action: str) -> str:
    """שם הפעולה בשפה הפעילה (director.action.*)."""
    return i18n.tr(f"director.action.{action}", default=action)
