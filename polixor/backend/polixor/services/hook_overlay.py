"""
הוו העריכתי על המסך – טקסט קצר בתחילת השורט, נפרד מהכתוביות.

עיצוב: תווית נקייה (קופסה מעוגלת בהירה, טקסט כהה ומודגש), עד שתי שורות
שנמדדות בגופן בפועל, הופעה והיעלמות רכות בלבד – בלי אנימציות.

מיקום (לפי מה שבפריים בשניות הראשונות):
  * פריסת תגובה / מסך מפוצל: על קו התפר בין פאנל התוכן לפאנל המצלמה –
    לא על הפנים ולא באמצע התוכן.
  * פנים / מסך / רקע מטושטש: באזור הבטוח העליון (מתחת לממשק האפליקציה).
  * תמיד מחוץ לרצועת הכתוביות; אם המקום המועדף מתנגש – המקום הבא בתור,
    ואם אין מקום שעומד בכל התנאים – לא מציירים (ולא מכסים פנים).

הוא נכתב כשכבה נוספת לקובץ ה-ASS של הכתוביות (אותו מעבר צריבה), או לקובץ
משלו כשהכתוביות כבויות.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from . import lang as _lang

# גודל ושוליים (חלק מגובה/רוחב הפריים)
FONT_FRAC = {True: 0.040, False: 0.055}         # אנכי / אופקי
MIN_SCALE = 0.72
MAX_WIDTH_FRAC = 0.84
TOP_SAFE = {True: 0.115, False: 0.07}           # מתחת לממשק של TikTok/Reels
PAD_X_EM, PAD_Y_EM, RADIUS_EM = 0.62, 0.36, 0.30
LINE_GAP = 1.18
BOX_COLOR, BOX_OPACITY, TEXT_COLOR = "#FFFFFF", 0.94, "#111111"
FADE_IN_MS, FADE_OUT_MS = 140, 220
RLM, LRM = "‏", "‎"
# פנים בפריים מלא (בלי נתוני פנים מדויקים): בדרך כלל בשליש העליון-אמצעי
FACE_ZONE = (0.20, 0.22, 0.80, 0.60)


@dataclass
class Placement:
    zone: str                  # seam | top | upper
    cx: float                  # מרכז אופקי (px)
    top: float                 # ראש הקופסה (px)
    box_w: float
    box_h: float
    font_px: int
    lines: list[str]

    @property
    def bottom(self) -> float:
        return self.top + self.box_h

    def rect(self, width: int, height: int) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) מנורמלים."""
        return ((self.cx - self.box_w / 2) / width, self.top / height,
                (self.cx + self.box_w / 2) / width, self.bottom / height)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def seconds_for(text: str) -> float:
    """מספיק זמן לקרוא, ולא יותר: ~0.32 ש׳ למילה, בין 2.2 ל-3.6 שניות."""
    return max(2.2, min(3.6, 1.2 + 0.32 * len(text.split())))


# --------------------------------------------------------------------------
# אזורים להימנע מהם
# --------------------------------------------------------------------------
def avoid_zones(plan: Any, seconds: float) -> tuple[list[tuple[float, float, float, float]], Optional[float]]:
    """
    אזורים (מנורמלים) שאסור לכסות בשניות הראשונות, וקו התפר בין פאנלים אם יש.
    בפריסת תגובה – פאנל המצלמה כולו (שם הפנים).
    """
    zones: list[tuple[float, float, float, float]] = []
    seam: Optional[float] = None
    pieces = list(getattr(plan, "pieces", None) or [])
    for pc in pieces:
        if pc.start > seconds or pc.end < 0.0:
            continue
        if pc.kind in ("reaction", "split") and pc.cam is not None:
            cf = float(pc.content_frac or 0.5)
            if pc.order == "cam_top":
                cam = (0.0, 0.0, 1.0, 1.0 - cf)
                seam = 1.0 - cf
            else:
                cam = (0.0, cf, 1.0, 1.0)
                seam = cf
            zones.append(cam)
        elif pc.kind in ("camera", "face", "center"):
            # פנים בפריים מלא: בדרך כלל בשליש העליון-אמצעי
            zones.append(FACE_ZONE)
    if not pieces and plan is not None and getattr(plan, "layout", "") in ("face", "auto_face", "track"):
        zones.append(FACE_ZONE)
    return zones, seam


def _overlap(a: Sequence[float], b: Sequence[float]) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h


# --------------------------------------------------------------------------
# פריסה
# --------------------------------------------------------------------------
def _split(words: list[str], width_of, max_w: float) -> Optional[list[str]]:
    from .caption_engine import _is_hanging

    one = " ".join(words)
    if width_of(one) <= max_w:
        return [one]
    best, best_v = None, float("inf")
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        wa, wb = width_of(a), width_of(b)
        if max(wa, wb) > max_w:
            continue
        v = max(wa, wb) + abs(wa - wb) * 0.3
        if _is_hanging(words[i - 1]):
            v += max_w * 0.25
        if v < best_v:
            best, best_v = [a, b], v
    return best


def layout(text: str, *, width: int, height: int, font: str, weight: int = 800,
           plan: Any = None, subtitle_zone: Optional[tuple[float, float]] = None,
           measurer_factory=None) -> Optional[Placement]:
    """מיקום וגודל לוו, או None כשאין מקום שעומד בכל התנאים."""
    words = (text or "").split()
    if not words:
        return None
    vertical = width / max(1, height) < 1.2
    seconds = seconds_for(text)
    zones, seam = avoid_zones(plan, seconds)
    sub = (0.0, subtitle_zone[0], 1.0, subtitle_zone[1]) if subtitle_zone is not None else None
    if measurer_factory is None:
        from .subtitle_render import Measurer
        measurer_factory = lambda px: Measurer(font, weight, px)  # noqa: E731
    base_px = FONT_FRAC[vertical] * height
    max_w = MAX_WIDTH_FRAC * width
    for scale in (1.0, 0.9, 0.8, MIN_SCALE):
        px = max(10, int(round(base_px * scale)))
        m = measurer_factory(px)
        lines = _split(words, m.width, max_w - 2 * PAD_X_EM * px)
        if lines is None:
            continue
        text_w = max(m.width(ln) for ln in lines)
        box_w = text_w + 2 * PAD_X_EM * px
        box_h = len(lines) * px * LINE_GAP + 2 * PAD_Y_EM * px
        spots: list[tuple[str, float]] = []
        if seam is not None:
            # רובו מעל קו התפר, כך שרק שוליים עליונים של פאנל המצלמה מכוסים
            cam_below = not any(z[1] < seam - 1e-6 for z in zones)
            off = 0.72 if cam_below else 0.28
            spots.append(("seam", seam * height - off * box_h))
        spots.append(("top", TOP_SAFE[vertical] * height))
        spots.append(("upper", (TOP_SAFE[vertical] + 0.12) * height))
        for zone, top in spots:
            pl = Placement(zone=zone, cx=width / 2.0, top=round(top, 1), box_w=round(box_w, 1),
                           box_h=round(box_h, 1), font_px=px, lines=lines)
            r = pl.rect(width, height)
            if r[1] < 0.02 or r[3] > 0.98:
                continue
            area = (r[2] - r[0]) * (r[3] - r[1])
            # על קו התפר מותר לגעת מעט בפאנל המצלמה (קצה הפאנל, לא הפנים)
            # מחוץ לתפר: מגע קטן בשולי אזור הפנים המשוער (המצח, לא הפנים) מותר
            allow = 0.35 if zone == "seam" else 0.12
            face_ok = all(_overlap(r, z) <= allow * area + 1e-9 for z in zones)
            sub_ok = sub is None or _overlap(r, sub) == 0.0
            if face_ok and sub_ok:
                return pl
    return None


# --------------------------------------------------------------------------
# ASS
# --------------------------------------------------------------------------
def _ass_color(hex_color: str, opacity: float = 1.0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = int(round(255 * (1.0 - max(0.0, min(1.0, opacity)))))
    return f"&H{a:02X}{b}{g}{r}".upper()


def style_line(font: str, font_px: int) -> str:
    text = _ass_color(TEXT_COLOR)
    return (f"Style: PolixorHook,{font},{font_px},{text},{text},&H00000000,&H00000000,"
            f"-1,0,0,0,100,100,0,0,1,0,0,8,0,0,0,-1")


def events(pl: Placement, text: str, *, language: str = "", start: float = 0.0) -> list[str]:
    from ..util.text import ass_escape, format_timestamp
    from .subtitle_render import _rounded_rect

    seconds = seconds_for(text)
    t0 = format_timestamp(start, style="ass")
    t1 = format_timestamp(start + seconds, style="ass")
    fade = f"\\fad({FADE_IN_MS},{FADE_OUT_MS})"
    x0 = pl.cx - pl.box_w / 2
    radius = RADIUS_EM * pl.font_px
    box_c = _ass_color(BOX_COLOR)[4:]
    alpha = int(round(255 * (1.0 - BOX_OPACITY)))
    shape = _rounded_rect(pl.box_w, pl.box_h, radius)
    box = (f"Dialogue: 5,{t0},{t1},PolixorHook,,0,0,0,,{{\\an7\\pos({x0:.1f},{pl.top:.1f})"
           f"\\bord0\\shad0\\c&H{box_c}&\\1a&H{alpha:02X}&{fade}\\p1}}{shape}{{\\p0}}")
    pack = _lang.get_pack(language) if language else None
    mark = RLM if (pack is not None and pack.direction == "rtl") or _is_rtl(text) else LRM
    body = "\\N".join(mark + ass_escape(ln) for ln in pl.lines)
    pad_y = PAD_Y_EM * pl.font_px
    line_tag = f"\\fs{pl.font_px}"
    txt = (f"Dialogue: 6,{t0},{t1},PolixorHook,,0,0,0,,{{\\an8\\pos({pl.cx:.1f},{pl.top + pad_y:.1f})"
           f"{line_tag}\\bord0\\shad0{fade}}}{body}")
    return [box, txt]


def _is_rtl(text: str) -> bool:
    return any("֐" <= ch <= "׿" or "؀" <= ch <= "ۿ" for ch in text)


def inject(ass_path: Path, style: str, evs: Sequence[str]) -> Path:
    """מוסיף את הסגנון והאירועים לקובץ ASS קיים."""
    text = Path(ass_path).read_text("utf-8")
    lines = text.splitlines()
    out: list[str] = []
    added = False
    for ln in lines:
        out.append(ln)
        if not added and ln.startswith("Style: "):
            out.append(style)
            added = True
    if not added:
        raise ValueError("no [V4+ Styles] section")
    out.extend(evs)
    Path(ass_path).write_text("\n".join(out) + "\n", "utf-8")
    return Path(ass_path)


def standalone(path: Path, *, width: int, height: int, style: str, evs: Sequence[str]) -> Path:
    """קובץ ASS עם הוו בלבד (כשהכתוביות כבויות)."""
    head = (
        "[Script Info]\n; Polixor editorial hook\nScriptType: v4.00+\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(head + "\n".join(evs) + "\n", "utf-8")
    return Path(path)


def subtitle_zone(style: Any, *, v2: bool, width: int, height: int) -> tuple[float, float]:
    """הרצועה (מנורמלת, y0..y1) שהכתוביות יכולות לתפוס – שתי שורות ושוליים."""
    if v2:
        from .subtitle_render import geometry
        from .subtitle_style import clamp_style

        st = clamp_style(style)
        geo = geometry(st, width, height)
        lines = int(st.get("max_lines", 2) or 2)
        band = (lines * geo.font_px * 1.3 + 2 * geo.pad_y + 2 * geo.outline_px) / height
        pos = st["position"]
        margin = geo.margin_v / height
    else:
        px = float(getattr(style, "font_size", 0) or 0.05 * height)
        lines = int(getattr(style, "max_lines", 2) or 2)
        band = (lines * px * 1.3) / height
        pos = getattr(style, "position", "bottom")
        margin = float(getattr(style, "margin_v", 0) or 0) / height
    if pos == "top":
        return (max(0.0, margin - 0.01), min(1.0, margin + band + 0.01))
    if pos == "middle":
        return (max(0.0, 0.5 - band / 2 - 0.01), min(1.0, 0.5 + band / 2 + 0.01))
    return (max(0.0, 1.0 - margin - band - 0.01), min(1.0, 1.0 - margin + 0.01))


def apply_to_clip(text: str, *, sub_path: Optional[Path], parts: Sequence[Optional[Path]],
                  work_dir: Path, clip_id: str, width: int, height: int, font: str,
                  plan: Any, style: Any, v2: bool, subtitles_on: bool,
                  language: str = "") -> tuple[Optional[Path], list[Optional[Path]], dict[str, Any]]:
    """
    מוסיף את הוו לקובץ ה-ASS של הקליפ (או יוצר קובץ משלו). מחזיר את הנתיבים
    המעודכנים ורשומה ל-render_params (טקסט, מיקום, זמן – או סיבה שלא צויר).
    """
    text = (text or "").strip()
    if not text:
        return sub_path, list(parts), {}
    zone = subtitle_zone(style, v2=v2, width=width, height=height) if subtitles_on else None
    pl = layout(text, width=width, height=height, font=font, plan=plan, subtitle_zone=zone)
    if pl is None:
        return sub_path, list(parts), {"text": text, "rendered": False, "reason": "no_safe_space"}
    sty = style_line(font, pl.font_px)
    evs = events(pl, text, language=language)
    parts = list(parts)
    if sub_path is not None and Path(sub_path).exists():
        inject(sub_path, sty, evs)
    else:
        sub_path = standalone(work_dir / f"{clip_id}_hook.ass", width=width, height=height,
                              style=sty, evs=evs)
    if parts:
        if parts[0] is not None and Path(parts[0]).exists():
            inject(parts[0], sty, evs)
        else:
            parts[0] = standalone(work_dir / f"{clip_id}_hook_part000.ass", width=width,
                                  height=height, style=sty, evs=evs)
    return sub_path, parts, {"text": text, "rendered": True, "seconds": seconds_for(text),
                             "placement": pl.to_dict(),
                             "rect": [round(x, 4) for x in pl.rect(width, height)],
                             "subtitle_zone": [round(x, 4) for x in zone] if zone else None}
