"""
הרכבת פריים אנכי (9:16, 1:1, 4:5) מתוך פריסת המקור.

לכל קטע בזמן יש "חלק" (Piece) עם אופן ההצגה שלו:

  reaction   התוכן והמצלמה בפאנלים נפרדים, אחד מעל השני. גובה פאנל
             התוכן נגזר מיחס הרוחב-גובה שלו, ופאנל המצלמה מקבל את השאר.
             חיתוך המצלמה נעשה בתוך חלון המצלמה בלבד, סביב הפנים ועם
             שוליים – כך שלא נכנס אליו תוכן ולא נחתכות הפנים.
  camera     חיתוך שעוקב אחרי הפנים (כשהמצלמה ממלאת את הפריים).
  screen     תוכן בלבד: חיתוך, או – כשחיתוך היה מאבד יותר מ-30% מהתוכן –
             התוכן המלא על רקע מטושטש של עצמו.
  center / blur / split   הפריסות הקודמות, לתאימות.

המודול מחזיר מחרוזות פילטר של FFmpeg. כל התוויות בפילטר מקבלות קידומת
ייחודית (`label`), כי כמה חלקים יכולים להופיע באותו גרף – תוויות
כפולות שוברות את הגרף.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .layout_detect import Box, LayoutSegment

PIECE_KINDS = ("reaction", "camera", "screen", "center", "blur", "split", "face")

# פאנל התוכן בתגובה: לא פחות מ-30% ולא יותר מ-62% מגובה הפריים
CONTENT_PANEL_RANGE = (0.30, 0.62)
# חיתוך שמשאיר פחות מזה מרוחב התוכן הופך להתאמה על רקע מטושטש
MIN_CROP_KEEP = 0.70
# שוליים סביב הפנים בתוך פאנל המצלמה
FACE_MARGIN = 0.30


@dataclass
class CropSeg:
    """קטע תנועה של מרכז החיתוך (x מנורמל) בזמן יחסי לקליפ."""

    t0: float
    t1: float
    x0: float
    x1: float

    def to_dict(self) -> dict[str, float]:
        return {"t0": round(self.t0, 3), "t1": round(self.t1, 3),
                "x0": round(self.x0, 4), "x1": round(self.x1, 4)}


@dataclass
class Piece:
    start: float                      # זמן יחסי לתחילת הקליפ
    end: float
    kind: str
    content: Optional[Box] = None     # חיתוך התוכן במקור (כבר ביחס של הפאנל)
    cam: Optional[Box] = None         # חיתוך המצלמה במקור (כבר ביחס של הפאנל)
    order: str = "content_top"        # content_top | cam_top
    content_frac: float = 0.5         # חלק הגובה של פאנל התוכן
    fit: str = "crop"                 # screen: crop | fit
    path: list[CropSeg] = field(default_factory=list)   # camera/face
    center_x: float = 0.5
    note: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def is_composite(self) -> bool:
        return self.kind in ("reaction", "split", "blur") or \
            (self.kind == "screen" and self.fit == "fit")

    def shifted(self, dt: float) -> "Piece":
        return Piece(start=self.start + dt, end=self.end + dt, kind=self.kind,
                     content=self.content, cam=self.cam, order=self.order,
                     content_frac=self.content_frac, fit=self.fit,
                     path=[CropSeg(s.t0 + dt, s.t1 + dt, s.x0, s.x1) for s in self.path],
                     center_x=self.center_x, note=self.note)

    def clipped(self, t0: float, t1: float) -> Optional["Piece"]:
        s, e = max(self.start, t0), min(self.end, t1)
        if e - s <= 1e-3:
            return None
        p = self.shifted(0.0)
        p.start, p.end = s, e
        return p

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3), "end": round(self.end, 3),
            "kind": self.kind,
            "content": self.content.to_dict() if self.content else None,
            "cam": self.cam.to_dict() if self.cam else None,
            "order": self.order, "content_frac": round(self.content_frac, 4),
            "fit": self.fit, "path": [s.to_dict() for s in self.path],
            "center_x": round(self.center_x, 4),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Piece":
        return cls(
            start=float(d.get("start", 0.0)), end=float(d.get("end", 0.0)),
            kind=str(d.get("kind") or "center"),
            content=Box.from_dict(d.get("content")), cam=Box.from_dict(d.get("cam")),
            order=str(d.get("order") or "content_top"),
            content_frac=float(d.get("content_frac") or 0.5),
            fit=str(d.get("fit") or "crop"),
            path=[CropSeg(float(s["t0"]), float(s["t1"]), float(s["x0"]), float(s["x1"]))
                  for s in d.get("path") or []],
            center_x=float(d.get("center_x") or 0.5))


# --------------------------------------------------------------------------
# תכנון
# --------------------------------------------------------------------------
def _fit_aspect(box: Box, aspect: float, src_w: int, src_h: int, *,
                focus_x: float, focus_y: float) -> Box:
    """
    חותך מלבן (בתוך `box`) ליחס רוחב-גובה נתון, סביב נקודת מיקוד.
    התוצאה תמיד בתוך המלבן המקורי.
    """
    bw_px, bh_px = box.w * src_w, box.h * src_h
    if bw_px <= 0 or bh_px <= 0:
        return box
    if bw_px / bh_px > aspect:
        new_w = bh_px * aspect / src_w
        x = min(box.x1 - new_w, max(box.x, focus_x - new_w / 2.0))
        return Box(x, box.y, new_w, box.h)
    new_h = bw_px / aspect / src_h
    y = min(box.y1 - new_h, max(box.y, focus_y - new_h / 2.0))
    return Box(box.x, y, box.w, new_h)


def plan_reaction(seg: LayoutSegment, *, out_w: int, out_h: int, src_w: int,
                  src_h: int, order: str = "auto") -> Optional[Piece]:
    """
    תכנון פריים תגובה: תוכן + מצלמה, כשהפנים נשארות שלמות.

    מחזיר None כשאין מספיק מידע (אין חלון מצלמה או אין אזור תוכן).
    """
    cam, content, face = seg.facecam, seg.content, seg.face
    if cam is None or content is None or content.area < 0.05:
        return None
    face = face or cam.expand(0.45, 0.5)
    if order not in ("content_top", "cam_top"):
        order = "cam_top" if cam.cy < 0.5 else "content_top"

    content_aspect = content.aspect(src_w, src_h)
    lo, hi = CONTENT_PANEL_RANGE
    frac = min(hi, max(lo, (out_w / content_aspect) / out_h))

    # מקטינים את פאנל התוכן עד שהפנים (עם שוליים) נכנסות לפאנל המצלמה
    needed = face.expand(1.0 + FACE_MARGIN, 1.0 + FACE_MARGIN * 1.2).clamp()
    for _ in range(20):
        cam_h = out_h * (1.0 - frac)
        cam_aspect = out_w / max(1.0, cam_h)
        # ראש קצת מעל המרכז: הפנים יושבות בשליש העליון של הפאנל
        crop = _fit_aspect(cam, cam_aspect, src_w, src_h,
                           focus_x=face.cx, focus_y=face.cy + 0.08 * face.h)
        if crop.contains(needed, tol=0.004) or frac <= lo + 1e-6:
            break
        frac = max(lo, frac - 0.04)
    content_h = out_h * frac
    content_crop = _fit_aspect(content, out_w / max(1.0, content_h), src_w, src_h,
                               focus_x=content.cx, focus_y=content.cy)
    return Piece(start=seg.start, end=seg.end, kind="reaction",
                 content=content_crop, cam=crop, order=order,
                 content_frac=round(frac, 4))


def plan_screen(seg: LayoutSegment, *, out_w: int, out_h: int, src_w: int,
                src_h: int) -> Piece:
    """תוכן בלבד: חיתוך כשזה משאיר מספיק, אחרת התאמה על רקע מטושטש."""
    content = seg.content or Box(0.0, 0.0, 1.0, 1.0)
    target = out_w / out_h
    aspect = content.aspect(src_w, src_h)
    keep = min(1.0, target / aspect) if aspect > 0 else 1.0
    if keep >= MIN_CROP_KEEP:
        crop = _fit_aspect(content, target, src_w, src_h,
                           focus_x=(seg.focus or (content.cx, content.cy))[0],
                           focus_y=content.cy)
        return Piece(start=seg.start, end=seg.end, kind="screen", content=crop,
                     fit="crop")
    return Piece(start=seg.start, end=seg.end, kind="screen", content=content,
                 fit="fit")


def plan_camera(seg: LayoutSegment, *, path: Optional[list[CropSeg]] = None) -> Piece:
    cx = seg.face.cx if seg.face is not None else 0.5
    return Piece(start=seg.start, end=seg.end, kind="camera",
                 path=list(path or []), center_x=cx)


def path_from_samples(samples: list[tuple[float, float, float]], *, t0: float,
                      t1: float, deadzone: float = 0.035,
                      max_speed: float = 0.12) -> list[CropSeg]:
    """
    מסלול חיתוך מדגימות פנים (זמן, x, y). הדגימות מוחלקות ומוגבלות
    במהירות (`max_speed` = חלק מרוחב הפריים לשנייה), כדי שהמסגרת לא
    תרעד ולא תקפוץ.
    """
    pts = sorted((t, x) for t, x, _ in samples if t0 - 1.0 <= t <= t1 + 1.0)
    if not pts:
        return []
    grid_step = 0.2
    n = max(2, int(math.ceil((t1 - t0) / grid_step)) + 1)
    ts = [t0 + i * grid_step for i in range(n)]
    xs_src = [p[0] for p in pts]
    ys_src = [p[1] for p in pts]
    import numpy as np

    xs = np.interp(ts, xs_src, ys_src)
    win = max(1, int(round(1.2 / grid_step)))
    if win > 1 and xs.size > win:
        kernel = np.ones(win) / win
        padded = np.pad(xs, win // 2, mode="edge")
        xs = np.convolve(padded, kernel, mode="same")[win // 2: win // 2 + len(ts)]
    step_max = max_speed * grid_step
    for i in range(1, len(xs)):
        d = xs[i] - xs[i - 1]
        if abs(d) > step_max:
            xs[i] = xs[i - 1] + math.copysign(step_max, d)
    segs: list[CropSeg] = []
    start_i, anchor = 0, float(xs[0])
    for i in range(1, len(xs)):
        if abs(float(xs[i]) - anchor) > deadzone:
            segs.append(CropSeg(ts[start_i], ts[i], anchor, float(xs[i])))
            start_i, anchor = i, float(xs[i])
    segs.append(CropSeg(ts[start_i], t1, anchor, anchor))
    return segs[:60]


# --------------------------------------------------------------------------
# פילטרים
# --------------------------------------------------------------------------
def _crop_expr(box: Box) -> str:
    return (f"crop=w=trunc(iw*{box.w:.5f}/2)*2:h=trunc(ih*{box.h:.5f}/2)*2:"
            f"x=iw*{box.x:.5f}:y=ih*{box.y:.5f}")


def _center_expression(path: list[CropSeg], fallback: float) -> str:
    if not path:
        return f"{fallback:.5f}"
    if len(path) == 1 and abs(path[0].x1 - path[0].x0) < 1e-4:
        return f"{path[0].x0:.5f}"
    expr = f"{path[-1].x1:.5f}"
    for s in reversed(path):
        span = max(1e-6, s.t1 - s.t0)
        lerp = (f"({s.x0:.5f}+({s.x1 - s.x0:.5f})*"
                f"max(0\\,min(1\\,(t-{s.t0:.4f})/{span:.4f})))")
        expr = f"if(lt(t\\,{s.t1:.4f})\\,{lerp}\\,{expr})"
    return expr


def follow_filter(path: list[CropSeg], center_x: float, *, out_w: int,
                  out_h: int) -> str:
    ratio = out_w / out_h
    crop_w = f"min(in_w\\,in_h*{ratio:.6f})"
    crop_h = f"min(in_h\\,in_w/{ratio:.6f})"
    center = _center_expression(path, center_x)
    x_expr = f"max(0\\,min(in_w-{crop_w}\\,({center})*in_w-({crop_w})/2))"
    return (f"crop=w={crop_w}:h={crop_h}:x='{x_expr}':y=(in_h-{crop_h})/2,"
            f"scale={out_w}:{out_h}:flags=lanczos,setsar=1")


def blur_fit_filter(out_w: int, out_h: int, label: str,
                    content: Optional[Box] = None) -> str:
    """התוכן המלא במרכז, על רקע מטושטש של עצמו (שום דבר לא נחתך)."""
    pre = f"{_crop_expr(content)}," if content is not None and content.area < 0.995 else ""
    ratio = out_w / out_h
    return (
        f"{pre}split=2[{label}bg][{label}fg];"
        f"[{label}bg]crop=w='min(in_w\\,in_h*{ratio:.6f})':"
        f"h='min(in_h\\,in_w/{ratio:.6f})':x=(in_w-out_w)/2:y=(in_h-out_h)/2,"
        f"scale={out_w}:{out_h}:flags=fast_bilinear,"
        f"gblur=sigma=26,eq=brightness=-0.10:saturation=0.85,setsar=1[{label}bgb];"
        f"[{label}fg]scale='min({out_w}\\,iw*{out_h}/ih)':'min({out_h}\\,ih*{out_w}/iw)'"
        f":flags=lanczos,setsar=1[{label}fgs];"
        f"[{label}bgb][{label}fgs]overlay=x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2,"
        f"scale={out_w}:{out_h}:flags=bicubic,setsar=1"
    )


def reaction_filter(piece: Piece, *, out_w: int, out_h: int, label: str) -> str:
    """שני פאנלים: תוכן ומצלמה, לפי הסדר של החלק."""
    content_h = int(round(out_h * piece.content_frac)) // 2 * 2
    cam_h = out_h - content_h
    assert piece.content is not None and piece.cam is not None
    content = (f"[{label}a]{_crop_expr(piece.content)},"
               f"scale={out_w}:{content_h}:flags=lanczos,setsar=1[{label}c]")
    cam = (f"[{label}b]{_crop_expr(piece.cam)},"
           f"scale={out_w}:{cam_h}:flags=lanczos,setsar=1[{label}k]")
    first, second = (f"[{label}k]", f"[{label}c]") if piece.order == "cam_top" \
        else (f"[{label}c]", f"[{label}k]")
    return (f"split=2[{label}a][{label}b];{content};{cam};"
            f"{first}{second}vstack=inputs=2,setsar=1")


def split_filter(region: dict[str, float], *, out_w: int, out_h: int,
                 label: str) -> str:
    """מסך מפוצל ישן: אזור מצלמה ידני למעלה, מרכז הפריים למטה."""
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
        f"split=2[{label}cs][{label}gs];"
        f"[{label}cs]crop=w=in_w*{rw:.4f}:h=in_h*{rh:.4f}:"
        f"x=in_w*{rx:.4f}:y=in_h*{ry:.4f},"
        f"crop=w='min(in_w\\,in_h*{cam_ratio:.6f})':h='min(in_h\\,in_w/{cam_ratio:.6f})':"
        f"x=(in_w-out_w)/2:y=(in_h-out_h)/2,"
        f"scale={out_w}:{cam_h}:flags=lanczos,setsar=1[{label}cam];"
        f"[{label}gs]crop=w='min(in_w\\,in_h*{game_ratio:.6f})':"
        f"h='min(in_h\\,in_w/{game_ratio:.6f})':"
        f"x=(in_w-out_w)/2:y=(in_h-out_h)/2,"
        f"scale={out_w}:{game_h}:flags=lanczos,setsar=1[{label}game];"
        f"[{label}cam][{label}game]vstack=inputs=2"
    )


def piece_filter(piece: Piece, *, out_w: int, out_h: int, label: str,
                 camera_region: Optional[dict[str, float]] = None) -> str:
    """מחרוזת הפילטר לחלק אחד."""
    if piece.kind == "reaction" and piece.content is not None and piece.cam is not None:
        return reaction_filter(piece, out_w=out_w, out_h=out_h, label=label)
    if piece.kind == "screen":
        if piece.fit == "fit":
            return blur_fit_filter(out_w, out_h, label, piece.content)
        box = piece.content or Box(0.0, 0.0, 1.0, 1.0)
        return f"{_crop_expr(box)},scale={out_w}:{out_h}:flags=lanczos,setsar=1"
    if piece.kind in ("camera", "face"):
        return follow_filter(piece.path, piece.center_x, out_w=out_w, out_h=out_h)
    if piece.kind == "blur":
        return blur_fit_filter(out_w, out_h, label, piece.content)
    if piece.kind == "split" and camera_region:
        return split_filter(camera_region, out_w=out_w, out_h=out_h, label=label)
    return follow_filter([], 0.5, out_w=out_w, out_h=out_h)
