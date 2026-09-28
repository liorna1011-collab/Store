"""
Smart Reframe – המרה מ-16:9 לפריים צר יותר (9:16, 1:1, 4:5) תוך
שמירה על מה שחשוב בתמונה.

פריסות:
  auto       לפי פריסת המקור שזוהתה בכל קטע (`layout_detect`): תגובה →
             תוכן + מצלמה, מצלמה מלאה → מעקב פנים, מסך בלבד → חיתוך או
             התאמה על רקע מטושטש. קליפ שחוצה כמה פריסות מקבל כמה "חלקים".
  reaction   כמו auto, אבל גם כשלא זוהה חלון מצלמה – לפי אזור מצלמה
             ידני או משוער.
  auto_face  מעקב אחרי הפנים הדומיננטיות. נבנה מסלול חיתוך מוחלק
             עם "אזור מת" כדי שהמסגרת לא תרעד.
  center     חיתוך מרכזי קבוע. תמיד עובד, ללא תלות בזיהוי.
  split      מסך מפוצל: אזור מצלמה למעלה, מרכז הפריים למטה.
  blur_pad   הפריים המלא על רקע מטושטש.

הערה על דיוק: הזיהוי מבוסס על מסווגי Haar של OpenCV. כאשר הפנים
מסתובבות או שהתאורה חלשה, המעקב עלול לאבד אחיזה – במקרה כזה המסגרת
נשארת במיקום האחרון היציב.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from .. import i18n
from . import composition
from .composition import CropSeg, Piece
from .layout_detect import Box, LayoutSegment, LayoutTimeline
from .visual import FaceBox, VisualFeatures

log = logging.getLogger("polixor.reframe")


@dataclass
class CropSegment:
    t0: float
    t1: float
    x0: float       # מרכז אופקי מנורמל 0..1 בתחילת הקטע
    x1: float       # ובסופו


@dataclass
class ReframePlan:
    layout: str = "center"
    segments: list[CropSegment] = field(default_factory=list)
    camera_region: Optional[dict[str, float]] = None
    tracked_ratio: float = 0.0       # אחוז הזמן שבו נמצאו פנים
    note: str = ""
    # חלקים בזמן (auto / reaction). ריק => הפריסה הישנה לכל הקליפ.
    pieces: list[Piece] = field(default_factory=list)
    order: str = "auto"

    # ---- חלקים ----
    @property
    def is_time_dependent(self) -> bool:
        """יותר מחלק אחד: צריך להחליף הרכבה באמצע הקליפ."""
        return len(self.pieces) > 1

    @property
    def has_composite(self) -> bool:
        if self.pieces:
            return any(p.is_composite for p in self.pieces)
        return self.layout in ("split", "blur_pad")

    def restrict(self, t0: float, t1: float) -> "ReframePlan":
        """התכנית לטווח [t0, t1] באותו ציר זמן (בלי הזזה)."""
        return self._clip(t0, t1, shift=0.0)

    def slice(self, t0: float, t1: float) -> "ReframePlan":
        """התכנית לטווח [t0, t1], בזמנים יחסיים ל-t0."""
        return self._clip(t0, t1, shift=-t0)

    def shift(self, dt: float) -> "ReframePlan":
        return self._clip(-1e9, 1e9, shift=dt)

    def _clip(self, t0: float, t1: float, *, shift: float) -> "ReframePlan":
        pieces = []
        for p in self.pieces:
            c = p.clipped(t0, t1)
            if c is not None:
                pieces.append(c.shifted(shift))
        segs = []
        for s in self.segments:
            a, b = max(s.t0, t0), min(s.t1, t1)
            if b - a <= 1e-4:
                continue
            span = max(1e-6, s.t1 - s.t0)
            xa = s.x0 + (s.x1 - s.x0) * (a - s.t0) / span
            xb = s.x0 + (s.x1 - s.x0) * (b - s.t0) / span
            segs.append(CropSegment(a + shift, b + shift, xa, xb))
        if self.segments and not segs:
            # מחוץ לטווח – מחזיקים את המיקום הקרוב
            ref = self.segments[0] if t1 <= self.segments[0].t0 else self.segments[-1]
            x = ref.x0 if t1 <= ref.t0 else ref.x1
            segs = [CropSegment(t0 + shift, t1 + shift, x, x)]
        return ReframePlan(layout=self.layout, segments=segs,
                           camera_region=self.camera_region,
                           tracked_ratio=self.tracked_ratio, note=self.note,
                           pieces=pieces, order=self.order)

    def piece_bounds(self) -> list[float]:
        """זמני המעבר בין חלקים (לפיצול ביטים)."""
        return [p.start for p in self.pieces[1:]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "segments": [{"t0": round(s.t0, 3), "t1": round(s.t1, 3),
                          "x0": round(s.x0, 4), "x1": round(s.x1, 4)}
                         for s in self.segments],
            "camera_region": self.camera_region,
            "tracked_ratio": round(self.tracked_ratio, 3),
            "note": self.note,
            "pieces": [p.to_dict() for p in self.pieces],
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> Optional["ReframePlan"]:
        if not d:
            return None
        return cls(
            layout=str(d.get("layout") or "center"),
            segments=[CropSegment(float(s["t0"]), float(s["t1"]),
                                  float(s["x0"]), float(s["x1"]))
                      for s in d.get("segments") or []],
            camera_region=d.get("camera_region"),
            tracked_ratio=float(d.get("tracked_ratio") or 0.0),
            note=str(d.get("note") or ""),
            pieces=[Piece.from_dict(p) for p in d.get("pieces") or []],
            order=str(d.get("order") or "auto"))

    # ---- ביטוי ישן (auto_face) ----
    def x_expression(self, crop_w_expr: str, in_w_expr: str = "in_w") -> str:
        """
        ביטוי FFmpeg ל-x של פילטר crop.
        המרכז המנורמל מומר לפינה השמאלית ומוגבל לגבולות הפריים.
        """
        if not self.segments:
            return f"({in_w_expr}-{crop_w_expr})/2"

        center = self._center_expression()
        raw = f"({center})*{in_w_expr}-({crop_w_expr})/2"
        return f"max(0\\,min({in_w_expr}-{crop_w_expr}\\,{raw}))"

    def _center_expression(self) -> str:
        """ביטוי קטעי-לינארי של מרכז המסגרת כפונקציה של t."""
        segs = self.segments
        if len(segs) == 1:
            s = segs[0]
            if abs(s.x1 - s.x0) < 1e-4:
                return f"{s.x0:.5f}"
        expr = f"{segs[-1].x1:.5f}"
        for s in reversed(segs):
            span = max(1e-6, s.t1 - s.t0)
            lerp = (f"({s.x0:.5f}+({s.x1 - s.x0:.5f})*"
                    f"max(0\\,min(1\\,(t-{s.t0:.4f})/{span:.4f})))")
            expr = f"if(lt(t\\,{s.t1:.4f})\\,{lerp}\\,{expr})"
        return expr


def _note(key: str, lang: Optional[str] = None, **params: Any) -> str:
    return i18n.tr(f"layout.{key}", lang, **params)


# --------------------------------------------------------------------------
# תכנון
# --------------------------------------------------------------------------
def plan_reframe(
    visual: Optional[VisualFeatures],
    *,
    clip_start: float,
    clip_end: float,
    layout: str,
    target_aspect: float = 9.0 / 16.0,
    manual_camera: Optional[dict[str, float]] = None,
    deadzone: float = 0.035,
    smooth_seconds: float = 1.2,
    layouts: Optional[LayoutTimeline] = None,
    order: str = "auto",
    max_speed: float = 0.12,
    lang: Optional[str] = None,
    segments: Optional[list[tuple[float, float]]] = None,
    out_size: Optional[tuple[int, int]] = None,
    src_size: Optional[tuple[int, int]] = None,
) -> ReframePlan:
    """
    בונה תכנית מסגור לקליפ. הזמנים בתכנית יחסיים לתחילת הקליפ.

    `layouts` הוא ציר הפריסות של כל המקור; `out_size`/`src_size` משמשים
    לחישוב גובה הפאנלים בפריסת תגובה (ברירת מחדל: 1080 בגובה לפי היחס).
    """
    layout = {"face": "auto_face", "blur": "blur_pad"}.get(layout, layout)
    if layout in ("auto", "reaction"):
        plan = _plan_from_layouts(
            visual, layouts, clip_start=clip_start, clip_end=clip_end,
            force_reaction=(layout == "reaction"), target_aspect=target_aspect,
            manual_camera=manual_camera, order=order, deadzone=deadzone,
            smooth_seconds=smooth_seconds, max_speed=max_speed, lang=lang,
            out_size=out_size, src_size=src_size)
        if plan is not None:
            return plan
        # אין מידע על פריסה: חוזרים למעקב פנים, ואם אין – חיתוך מרכזי
        layout = "auto_face"

    if layout == "blur_pad":
        return ReframePlan(layout="blur_pad", note=_note("blur", lang))

    if layout == "center" or visual is None or not visual.analyzed:
        return ReframePlan(layout="center", note=_note("center", lang))

    if layout == "split":
        region = manual_camera or _camera_for_clip(visual, clip_start, clip_end)
        if region is None and layouts is not None:
            cam = layouts.dominant_facecam()
            region = cam.to_dict() if cam else None
        if region is None:
            return ReframePlan(layout="center", note=_note("split_no_camera", lang))
        return ReframePlan(layout="split", camera_region=region,
                           note=_note("split", lang))

    # auto_face
    path, tracked = _face_path(visual, clip_start, clip_end,
                               smooth_seconds=smooth_seconds, max_speed=max_speed)
    if path is None:
        return ReframePlan(layout="center", tracked_ratio=0.0,
                           note=_note("no_faces", lang))

    segs = _to_segments(path, clip_start, clip_end, deadzone=deadzone)
    return ReframePlan(
        layout="auto_face", segments=segs, tracked_ratio=tracked,
        note=_note("face_follow", lang, percent=f"{tracked * 100:.0f}",
                   moves=len(segs)))


def _plan_from_layouts(visual: Optional[VisualFeatures],
                       layouts: Optional[LayoutTimeline], *, clip_start: float,
                       clip_end: float, force_reaction: bool, target_aspect: float,
                       manual_camera: Optional[dict[str, float]], order: str,
                       deadzone: float, smooth_seconds: float, max_speed: float,
                       lang: Optional[str],
                       out_size: Optional[tuple[int, int]],
                       src_size: Optional[tuple[int, int]]) -> Optional[ReframePlan]:
    """חלק לכל קטע פריסה שנופל בתוך הקליפ."""
    out_h = out_size[1] if out_size else 1920
    out_w = out_size[0] if out_size else max(2, int(round(out_h * target_aspect)))
    src_w = src_size[0] if src_size else (layouts.src_w if layouts and layouts.src_w else 1920)
    src_h = src_size[1] if src_size else (layouts.src_h if layouts and layouts.src_h else 1080)
    duration = max(0.05, clip_end - clip_start)

    segs: list[LayoutSegment] = []
    if layouts is not None and layouts.analyzed and layouts.segments:
        segs = layouts.slice(clip_start, clip_end).segments

    if force_reaction and not any(s.kind == "reaction" for s in segs):
        cam = _camera_box(visual, layouts, manual_camera, clip_start, clip_end)
        if cam is None:
            return None
        face = cam.expand(0.45, 0.5)
        seg = LayoutSegment(start=0.0, end=duration, kind="reaction", facecam=cam,
                            face=face, confidence=0.5)
        from .layout_detect import _content_box  # noqa: PLC0415

        seg.content = _content_box([], [], cam)
        segs = [seg]
    if not segs:
        return None

    pieces: list[Piece] = []
    notes: list[str] = []
    tracked_total = 0.0
    for seg in segs:
        piece: Optional[Piece] = None
        if seg.kind == "reaction":
            piece = composition.plan_reaction(seg, out_w=out_w, out_h=out_h,
                                              src_w=src_w, src_h=src_h, order=order)
            if piece is not None:
                notes.append("reaction")
        if piece is None and seg.kind in ("reaction", "camera"):
            path = _camera_path(visual, seg, clip_start, deadzone=deadzone,
                                smooth_seconds=smooth_seconds, max_speed=max_speed)
            piece = composition.plan_camera(seg, path=path)
            tracked_total += seg.duration if (path or seg.face) else 0.0
            notes.append("camera")
        if piece is None:
            piece = composition.plan_screen(seg, out_w=out_w, out_h=out_h,
                                            src_w=src_w, src_h=src_h)
            notes.append("screen_fit" if piece.fit == "fit" else "screen_crop")
        pieces.append(piece)

    # כיסוי מלא של הקליפ: חורים בין קטעים ממולאים בחלק הקודם
    pieces.sort(key=lambda p: p.start)
    pieces[0].start = 0.0
    for a, b in zip(pieces, pieces[1:]):
        a.end = b.start
    pieces[-1].end = duration

    kinds = [p.kind for p in pieces]
    main = "reaction" if "reaction" in kinds else ("camera" if "camera" in kinds else "screen")
    layout_name = "reaction" if force_reaction or main == "reaction" else "auto"
    if len(pieces) == 1:
        note = _note(f"piece_{notes[0]}", lang)
    else:
        note = _note("pieces", lang, count=len(pieces),
                     kinds=" → ".join(_note(f"kind_{k}", lang) for k in kinds))
    return ReframePlan(layout=layout_name, pieces=pieces, note=note,
                       tracked_ratio=round(tracked_total / duration, 3),
                       camera_region=(pieces[0].cam.to_dict()
                                      if pieces[0].cam is not None else None),
                       order=order)


def _camera_box(visual: Optional[VisualFeatures], layouts: Optional[LayoutTimeline],
                manual: Optional[dict[str, float]], t0: float, t1: float
                ) -> Optional[Box]:
    if manual:
        return Box.from_dict(manual)
    if layouts is not None:
        cam = layouts.dominant_facecam()
        if cam is not None:
            return cam
    if visual is not None and visual.analyzed:
        region = _camera_for_clip(visual, t0, t1)
        if region:
            return Box.from_dict(region)
    return None


def _camera_path(visual: Optional[VisualFeatures], seg: LayoutSegment,
                 clip_start: float, *, deadzone: float, smooth_seconds: float,
                 max_speed: float) -> list[CropSeg]:
    """מסלול מעקב פנים לחלק מסוג מצלמה – מהניתוח החזותי, או מדגימות הפריסה."""
    t0, t1 = seg.start, seg.end
    if visual is not None and visual.analyzed and visual.faces:
        path, tracked = _face_path(visual, clip_start + t0, clip_start + t1,
                                   smooth_seconds=smooth_seconds, max_speed=max_speed)
        if path is not None and tracked >= 0.3:
            return [CropSeg(s.t0 + t0, s.t1 + t0, s.x0, s.x1)
                    for s in _to_segments(path, clip_start + t0, clip_start + t1,
                                          deadzone=deadzone)]
    if seg.face_path:
        return composition.path_from_samples(seg.face_path, t0=t0, t1=t1,
                                             deadzone=deadzone, max_speed=max_speed)
    return []


# --------------------------------------------------------------------------
# מסלול הפנים
# --------------------------------------------------------------------------
def _face_path(visual: VisualFeatures, clip_start: float, clip_end: float,
               *, smooth_seconds: float, max_speed: float = 0.12
               ) -> tuple[Optional[np.ndarray], float]:
    """
    מחזיר (מסלול מרכז אופקי מנורמל לכל פריים שנדגם, שיעור הפריימים
    שבהם נמצאו פנים). None אם לא נמצאו פנים כלל.

    ההחלקה נעשית על רשת צפופה (10 בשנייה) ולא על הדגימות עצמן: בדגימה
    של פריים לשנייה, חלון החלקה של 1.2 שניות הוא פריים אחד – כלומר
    ללא החלקה בכלל. גם הגבלת המהירות מחושבת לשנייה, ולא לפריים.
    """
    step = 1.0 / max(1e-6, visual.fps)
    i0 = visual.index_at(clip_start)
    i1 = min(len(visual.faces), visual.index_at(clip_end) + 1)
    if i1 <= i0:
        return None, 0.0

    raw = np.full(i1 - i0, np.nan, dtype=np.float32)
    for k, i in enumerate(range(i0, i1)):
        boxes = visual.faces[i] if i < len(visual.faces) else []
        best = _dominant_face(boxes)
        if best is not None:
            raw[k] = best[0] + best[2] / 2.0

    valid = ~np.isnan(raw)
    tracked = float(valid.mean()) if raw.size else 0.0
    if tracked < 0.08:
        return None, tracked

    # מילוי חורים ואינטרפולציה לרשת צפופה
    idx = np.arange(raw.size, dtype=np.float32)
    sample_t = idx * step
    filled = np.interp(idx, idx[valid], raw[valid]).astype(np.float32)
    grid_step = 0.1
    n_grid = max(2, int(math.ceil((raw.size - 1) * step / grid_step)) + 1)
    grid_t = np.arange(n_grid, dtype=np.float32) * grid_step
    dense = np.interp(grid_t, sample_t, filled).astype(np.float32)

    # החלקה: ממוצע נע על פני `smooth_seconds`
    win = max(1, int(round(smooth_seconds / grid_step)))
    if win > 1 and dense.size > 1:
        kernel = np.ones(win, dtype=np.float32) / win
        padded = np.pad(dense, win // 2, mode="edge")
        dense = np.convolve(padded, kernel, mode="same")[win // 2: win // 2 + dense.size]

    # הגבלת מהירות: לכל היותר `max_speed` מרוחב הפריים בשנייה
    limit = max_speed * grid_step
    for i in range(1, dense.size):
        d = dense[i] - dense[i - 1]
        if abs(d) > limit:
            dense[i] = dense[i - 1] + np.sign(d) * limit

    # חזרה לקצב הדגימה המקורי (הצרכנים מצפים לנקודה לכל פריים שנדגם)
    out = np.interp(sample_t, grid_t, dense).astype(np.float32)
    return np.clip(out, 0.0, 1.0).astype(np.float32), tracked


def _dominant_face(boxes: list[FaceBox]) -> Optional[FaceBox]:
    """הפנים הגדולות ביותר בפריים – בדרך כלל הסטרימר."""
    if not boxes:
        return None
    return max(boxes, key=lambda b: b[2] * b[3])


def _to_segments(path: np.ndarray, clip_start: float, clip_end: float,
                 *, deadzone: float) -> list[CropSegment]:
    """
    ממיר מסלול רציף לקטעי תנועה קומפקטיים: מיזוג פריימים שבהם
    המרכז כמעט לא זז, כדי לקבל ביטוי FFmpeg קצר ומסגרת יציבה.
    """
    n = path.size
    if n == 0:
        return []
    duration = max(1e-3, clip_end - clip_start)
    step = duration / n

    segments: list[CropSegment] = []
    seg_start_i = 0
    anchor = float(path[0])

    for i in range(1, n):
        if abs(float(path[i]) - anchor) > deadzone:
            t0 = seg_start_i * step
            t1 = i * step
            segments.append(CropSegment(t0=t0, t1=t1, x0=anchor, x1=float(path[i])))
            seg_start_i = i
            anchor = float(path[i])

    t0 = seg_start_i * step
    segments.append(CropSegment(t0=t0, t1=duration, x0=anchor, x1=anchor))

    # מיזוג קטעים קצרים מאוד כדי להימנע מקפיצות
    merged: list[CropSegment] = []
    for s in segments:
        if merged and (s.t1 - s.t0) < 0.25:
            merged[-1].t1 = s.t1
            merged[-1].x1 = s.x1
        else:
            merged.append(s)

    if len(merged) > 60:      # תקרת בטיחות לאורך הביטוי
        merged = _downsample_segments(merged, 60)
    return merged


def _downsample_segments(segs: list[CropSegment], target: int) -> list[CropSegment]:
    factor = max(2, len(segs) // target)
    out: list[CropSegment] = []
    for i in range(0, len(segs), factor):
        group = segs[i:i + factor]
        out.append(CropSegment(t0=group[0].t0, t1=group[-1].t1,
                               x0=group[0].x0, x1=group[-1].x1))
    return out


def _camera_for_clip(visual: VisualFeatures, clip_start: float,
                     clip_end: float) -> Optional[dict[str, float]]:
    """מאתר אזור מצלמה בתוך חלון הקליפ, ואם אין – על כל השידור."""
    from .visual import estimate_camera_region

    window = VisualFeatures(
        fps=visual.fps, duration=clip_end - clip_start,
        width=visual.width, height=visual.height,
        faces=visual.faces[visual.index_at(clip_start): visual.index_at(clip_end) + 1],
    )
    region = estimate_camera_region(window, min_hits=6)
    if region:
        return region
    return estimate_camera_region(visual, min_hits=12)


# --------------------------------------------------------------------------
# בניית גרף הפילטרים
# --------------------------------------------------------------------------
def build_vertical_filter(
    plan: ReframePlan,
    *,
    out_width: int,
    out_height: int,
    src_width: int,
    src_height: int,
    label: str = "r",
) -> str:
    """
    מחזיר שרשרת פילטרים (ללא כניסה/יציאה מסומנות) שממירה את הווידאו
    לפריים בגודל out_width x out_height.

    `label` הוא קידומת לתוויות הפנימיות – חייבת להיות ייחודית בכל גרף
    שבו מופיעות כמה שרשראות כאלה (למשל כמה ביטים).
    """
    if plan.pieces:
        # חלק יחיד (או התכנית כבר הוגבלה לטווח של ביט אחד)
        piece = plan.pieces[0]
        return composition.piece_filter(piece, out_w=out_width, out_h=out_height,
                                        label=label,
                                        camera_region=plan.camera_region)

    target_ratio = out_width / out_height

    if plan.layout == "blur_pad":
        return composition.blur_fit_filter(out_width, out_height, label)

    if plan.layout == "split" and plan.camera_region:
        return composition.split_filter(plan.camera_region, out_w=out_width,
                                        out_h=out_height, label=label)

    # חיתוך מתוך הפריים המקורי
    crop_w_expr = f"min(in_w\\,in_h*{target_ratio:.6f})"
    crop_h_expr = f"min(in_h\\,in_w/{target_ratio:.6f})"

    if plan.layout == "auto_face" and plan.segments:
        x_expr = plan.x_expression(crop_w_expr)
    else:
        x_expr = f"(in_w-{crop_w_expr})/2"

    return (
        f"crop=w={crop_w_expr}:h={crop_h_expr}:x='{x_expr}':y=(in_h-{crop_h_expr})/2,"
        f"scale={out_width}:{out_height}:flags=lanczos,setsar=1"
    )
