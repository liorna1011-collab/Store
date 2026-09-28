"""
Smart Reframe – המרה מ-16:9 ל-9:16 תוך שמירה על הדמות/האזור המעניין.

שלוש פריסות:
  center     – חיתוך מרכזי קבוע. תמיד עובד, ללא תלות בזיהוי.
  auto_face  – מעקב אחרי הפנים הדומיננטיות. נבנה מסלול חיתוך מוחלק
               עם "אזור מת" כדי שהמסגרת לא תרעד, ומקודד כביטוי
               קטעי-לינארי ל-FFmpeg.
  split      – מסך מפוצל: מצלמת הסטרימר למעלה, גיימפליי למטה.
               אזור המצלמה מזוהה אוטומטית או מוגדר ידנית ונשמר.

הערה על דיוק: המעקב מבוסס על מסווג Haar של OpenCV, שמזהה פנים
חזיתיות. כאשר הסטרימר מסתובב או שהתאורה חלשה, המעקב עלול לאבד
אחיזה – במקרה כזה המסגרת נשארת במיקום האחרון היציב.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

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
) -> ReframePlan:
    """בונה תכנית חיתוך לקליפ בודד."""
    if layout == "blur_pad":
        return ReframePlan(
            layout="blur_pad",
            note="הפריים המלא במרכז על רקע מטושטש – שום דבר לא נחתך, "
                 "והחדות נשמרת.")

    if layout == "center" or visual is None or not visual.analyzed:
        return ReframePlan(layout="center", note="חיתוך מרכזי קבוע.")

    if layout == "split":
        region = manual_camera or _camera_for_clip(visual, clip_start, clip_end)
        if region is None:
            return ReframePlan(
                layout="center",
                note="לא זוהה אזור מצלמה – בוצע חיתוך מרכזי במקום מסך מפוצל. "
                     "אפשר להגדיר את אזור המצלמה ידנית במסך העריכה.",
            )
        return ReframePlan(layout="split", camera_region=region,
                           note="מסך מפוצל: מצלמה למעלה, גיימפליי למטה.")

    # auto_face
    path, tracked = _face_path(visual, clip_start, clip_end,
                               smooth_seconds=smooth_seconds)
    if path is None:
        return ReframePlan(layout="center", tracked_ratio=0.0,
                           note="לא זוהו פנים בקטע – בוצע חיתוך מרכזי.")

    segments = _to_segments(path, clip_start, clip_end, deadzone=deadzone)
    return ReframePlan(
        layout="auto_face", segments=segments, tracked_ratio=tracked,
        note=(f"מעקב אחרי פנים ב-{tracked * 100:.0f}% מהקטע, "
              f"{len(segments)} קטעי תנועה."),
    )


# --------------------------------------------------------------------------
# מסלול הפנים
# --------------------------------------------------------------------------
def _face_path(visual: VisualFeatures, clip_start: float, clip_end: float,
               *, smooth_seconds: float) -> tuple[Optional[np.ndarray], float]:
    """
    מחזיר (מסלול מרכז אופקי מנורמל לכל פריים שנדגם, שיעור הפריימים
    שבהם נמצאו פנים). None אם לא נמצאו פנים כלל.
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

    # מילוי חורים: מחזיקים את הערך האחרון הידוע
    idx = np.arange(raw.size)
    filled = np.interp(idx, idx[valid], raw[valid]).astype(np.float32)

    # החלקה: ממוצע נע + הגבלת מהירות תזוזה
    win = max(1, int(round(smooth_seconds / step)))
    if win > 1:
        kernel = np.ones(win, dtype=np.float32) / win
        padded = np.pad(filled, win // 2, mode="edge")
        filled = np.convolve(padded, kernel, mode="same")[win // 2: win // 2 + raw.size]

    max_speed = 0.10 * step / max(1e-6, 1.0 / 30.0)   # ~10% רוחב לשנייה
    limited = filled.copy()
    for i in range(1, limited.size):
        d = limited[i] - limited[i - 1]
        if abs(d) > max_speed:
            limited[i] = limited[i - 1] + np.sign(d) * max_speed
    return np.clip(limited, 0.0, 1.0).astype(np.float32), tracked


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
) -> str:
    """
    מחזיר שרשרת פילטרים (ללא כניסה/יציאה מסומנות) שממירה את הווידאו
    לפריים אנכי בגודל out_width x out_height.
    """
    target_ratio = out_width / out_height

    if plan.layout == "blur_pad":
        return _blur_pad_filter(out_width, out_height)

    if plan.layout == "split" and plan.camera_region:
        return _split_filter(plan.camera_region, out_width, out_height,
                             src_width, src_height)

    # חיתוך אנכי מתוך הפריים המקורי
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


def _blur_pad_filter(out_w: int, out_h: int) -> str:
    """
    הפריים המלא במרכז, ומעליו ומתחתיו גרסה מוגדלת ומטושטשת של עצמו.

    היתרון על חיתוך: שום דבר לא יוצא מהפריים, ובעיקר – אין הגדלה
    של האזור המרכזי, ולכן התמונה נשארת חדה כמו במקור. הרקע מוכהה
    מעט כדי שהעין תלך למרכז.
    """
    return (
        f"split=2[bp_bg][bp_fg];"
        f"[bp_bg]crop=w='min(in_w\\,in_h*{out_w / out_h:.6f})':"
        f"h='min(in_h\\,in_w/{out_w / out_h:.6f})':"
        f"x=(in_w-out_w)/2:y=(in_h-out_h)/2,"
        f"scale={out_w}:{out_h}:flags=fast_bilinear,"
        f"gblur=sigma=26,eq=brightness=-0.10:saturation=0.85,setsar=1[bp_bgb];"
        f"[bp_fg]scale={out_w}:-2:flags=lanczos,setsar=1[bp_fgs];"
        f"[bp_bgb][bp_fgs]overlay=x=0:y=(main_h-overlay_h)/2,"
        f"scale={out_w}:{out_h}:flags=bicubic,setsar=1"
    )


def _split_filter(region: dict[str, float], out_w: int, out_h: int,
                  src_w: int, src_h: int) -> str:
    """
    מסך מפוצל: מצלמה בחלק העליון (38%), גיימפליי בתחתון (62%).
    שני החלקים נחתכים מהמקור, ולכן אין צורך בקבצים נוספים.
    """
    cam_h = int(out_h * 0.38) // 2 * 2
    game_h = (out_h - cam_h) // 2 * 2
    cam_h = out_h - game_h

    rx = max(0.0, min(0.95, float(region.get("x", 0.0))))
    ry = max(0.0, min(0.95, float(region.get("y", 0.0))))
    rw = max(0.05, min(1.0 - rx, float(region.get("w", 0.25))))
    rh = max(0.05, min(1.0 - ry, float(region.get("h", 0.25))))

    cam_ratio = out_w / max(1, cam_h)
    game_ratio = out_w / max(1, game_h)

    return (
        f"split=2[cam_src][game_src];"
        f"[cam_src]crop=w=in_w*{rw:.4f}:h=in_h*{rh:.4f}:"
        f"x=in_w*{rx:.4f}:y=in_h*{ry:.4f},"
        f"crop=w='min(in_w\\,in_h*{cam_ratio:.6f})':h='min(in_h\\,in_w/{cam_ratio:.6f})':"
        f"x=(in_w-out_w)/2:y=(in_h-out_h)/2,"
        f"scale={out_w}:{cam_h}:flags=lanczos,setsar=1[cam];"
        f"[game_src]crop=w='min(in_w\\,in_h*{game_ratio:.6f})':"
        f"h='min(in_h\\,in_w/{game_ratio:.6f})':"
        f"x=(in_w-out_w)/2:y=(in_h-out_h)/2,"
        f"scale={out_w}:{game_h}:flags=lanczos,setsar=1[game];"
        f"[cam][game]vstack=inputs=2"
    )
