"""
כתיבת כתוביות v2 ל-ASS: פריסה מדודה, RTL/LTR, רקעים ואנימציות.

מה שהמודול מבטיח (ונבדק ברינדור אמיתי ב-tests/test_subtitle_style.py):

  גודל     הגדלים בסגנון יחסיים לפריים (`subtitle_style`). ההמרה לפיקסלים
           נעשית כאן, פעם אחת, מגובה הפריים בפועל – ולכן אין „כיווץ כפול"
           ואין שינוי גודל בייצוא חוזר.
  שורות    רוחב כל שורה נמדד עם PIL על קובץ הגופן שבו libass ישתמש (דרך
           fontconfig), בגודל הפיקסלים של ה-ASS אחרי כיול מול libass עצמו.
           `words_per_line` ו-`max_lines` נאכפים: כתובית ארוכה מתפצלת
           לכמה כתוביות לפי תזמוני המילים. `WrapStyle: 2` – libass לא שובר
           שורות בעצמו.
  כיוון    `Encoding=-1` (כיוון בסיס אוטומטי ב-libass) ועוד סימן כיווניות
           (RLM/LRM) בתחילת כל שורה – כך שורה בעברית שמתחילה ב-„YouTube"
           עדיין נקראת מימין לשמאל, וסימן הפיסוק בסופה נמצא משמאל.
  גבולות   הכתובית בתוך האזור הבטוח של הפריים, ומילה שרחבה מהשורה מוקטנת
           (ולא יוצאת מהמסך).
  רקע      box: BorderStyle 3 (ב-libass הקופסה נצבעת ב-OutlineColour וצלה
           ב-BackColour – נבדק ברינדור). bar: פס ברוחב מלא, כציור וקטורי
           בשכבה מתחת לטקסט.
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import re
import subprocess
import tempfile
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from ..util.text import ass_escape, format_timestamp, hex_to_ass_color
from . import fonts as fontsvc
from . import lang as _lang
from .subtitle_style import SubtitleStyleV2, clamp_style
from .subtitles import Cue

log = logging.getLogger("polixor.subtitle_render")

RLM = "‏"
LRM = "‎"

# אזור בטוח: מרחק מינימלי מהקצה (כשבר מגובה/רוחב הפריים)
SAFE_ZONE = {
    True: {"top": 0.10, "bottom": 0.16, "side": 0.06},     # אנכי / צר
    False: {"top": 0.06, "bottom": 0.08, "side": 0.05},    # 16:9
}
# הגדלת המילה הפעילה באנימציות
ANIM_SCALE = {"pop": 1.12, "bounce": 1.20}


# --------------------------------------------------------------------------
# מדידת גופן, מכוילת מול libass
# --------------------------------------------------------------------------
@dataclass
class FontSpec:
    family: str
    weight: int
    file: Optional[Path]
    # טווחי התווים שהקובץ מכסה (None = לא ידוע, מניחים שהכול מכוסה)
    ranges: Optional[tuple[tuple[int, int], ...]] = None
    # גודל PIL לכל יחידת Fontsize של ASS (libass מנרמל את גובה הגופן)
    pil_per_ass: float = 0.72
    # libass מקטין כל גופן כך ש-ascent+descent = Fontsize; זה החלק שמעל קו הבסיס
    asc_ratio: float = 0.8
    calibrated: bool = False

    def covers(self, cp: int) -> Optional[bool]:
        if self.ranges is None:
            return None
        i = bisect.bisect_right(self.ranges, (cp, 0x10FFFF)) - 1
        return i >= 0 and self.ranges[i][0] <= cp <= self.ranges[i][1]


_SPEC_CACHE: dict[tuple[str, int], FontSpec] = {}
_SPEC_LOCK = threading.RLock()
_PIL_CACHE: dict[tuple[str, int], Any] = {}
_METRICS_VERSION = 2


def _disk_cache_path() -> Optional[Path]:
    try:
        from ..config import PATHS

        return PATHS.data / "font_metrics.json"
    except Exception:                                  # noqa: BLE001
        return None


def _load_disk_cache() -> dict[str, Any]:
    p = _disk_cache_path()
    if p and p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_disk_cache(data: dict[str, Any]) -> None:
    p = _disk_cache_path()
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=1), "utf-8")
    except OSError:
        pass


def font_spec(family: str, weight: int = 400) -> FontSpec:
    """המידות של גופן כפי ש-libass מרנדר אותו (מכויל פעם אחת ונשמר)."""
    weight = int(weight or 400)
    key = (family, weight)
    with _SPEC_LOCK:
        if key in _SPEC_CACHE:
            return _SPEC_CACHE[key]
    path = fontsvc.font_file(family, weight)
    spec = FontSpec(family=family, weight=weight, file=path, ranges=fontsvc.charset(path))
    disk = _load_disk_cache()
    dkey = f"v{_METRICS_VERSION}|{family}|{weight}|{path}"
    if dkey in disk:
        spec.pil_per_ass = float(disk[dkey]["pil_per_ass"])
        spec.asc_ratio = float(disk[dkey]["asc_ratio"])
        spec.calibrated = True
    elif path is not None:
        try:
            _calibrate(spec)
            if spec.calibrated:
                disk[dkey] = {"pil_per_ass": spec.pil_per_ass, "asc_ratio": spec.asc_ratio}
                _save_disk_cache(disk)
        except Exception as exc:                        # noqa: BLE001
            log.warning("font calibration failed for %s: %s", family, exc)
    with _SPEC_LOCK:
        _SPEC_CACHE[key] = spec
    return spec


def _pil_font(path: Path, size: int):
    from PIL import ImageFont

    key = (str(path), int(size))
    f = _PIL_CACHE.get(key)
    if f is None:
        f = ImageFont.truetype(str(path), max(4, int(size)))
        if len(_PIL_CACHE) > 256:
            _PIL_CACHE.clear()
        _PIL_CACHE[key] = f
    return f


def _samples_for(spec: FontSpec) -> Optional[tuple[str, str]]:
    """טקסט כיול (רוחב) וטקסט בלי יורדים (קו בסיס), באותיות שהגופן מכסה."""
    if spec.covers(ord("H")) is not False:
        return "HxgHxgHxgHxg", "HHHHHH"
    if spec.covers(0x05D0):
        return "אבגדהוזחטיכלמנ", "הההההה"
    return None


def _calibrate(spec: FontSpec) -> None:
    """
    משווה רוחב שורה שמרונדרת ב-libass לרוחב שמחושב ב-PIL, ומודד היכן
    קו הבסיס יושב. כך המדידה בזמן הפריסה תואמת לרינדור בפועל.
    """
    from ..util.ffmpeg import ffmpeg_bin
    from .render import escape_filter_path

    samples = _samples_for(spec)
    if samples is None or spec.file is None:
        return
    sample, base = samples
    size, width, height, base_top = 100, 2400, 460, 260
    ass = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: K,{spec.family},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,-1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:01.00,K,,0,0,0,,"
        f"{{\\an7\\pos(40,20)\\b{spec.weight}}}{sample}\n"
        f"Dialogue: 0,0:00:00.00,0:00:01.00,K,,0,0,0,,"
        f"{{\\an7\\pos(40,{base_top})\\b{spec.weight}}}{base}\n"
    )
    with tempfile.TemporaryDirectory(prefix="pxfont_") as d:
        ass_path = Path(d) / "k.ass"
        png = Path(d) / "k.png"
        ass_path.write_text(ass, encoding="utf-8")
        subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d=1",
             "-vf", f"ass='{escape_filter_path(ass_path)}'", "-frames:v", "1", str(png)],
            check=True, capture_output=True, timeout=60)
        import numpy as np
        from PIL import Image

        img = np.asarray(Image.open(png).convert("L"), dtype=np.uint8)
    mask = img > 110
    top, bottom = mask[:base_top - 10], mask[base_top - 10:]
    cols = np.flatnonzero(top.any(axis=0))
    rows = np.flatnonzero(bottom.any(axis=1))
    if cols.size == 0 or rows.size == 0:
        return
    libass_w = float(cols[-1] - cols[0] + 1)
    font = _pil_font(spec.file, size)
    bbox = font.getbbox(sample)
    pil_w = float(bbox[2] - bbox[0])
    if pil_w <= 0:
        return
    spec.pil_per_ass = libass_w / pil_w
    baseline = (base_top - 10) + int(rows[-1]) + 1
    spec.asc_ratio = min(0.98, max(0.5, (baseline - base_top) / size))
    spec.calibrated = True


def _script_of(ch: str) -> str:
    cp = ord(ch)
    if 0x0590 <= cp <= 0x05FF or 0xFB1D <= cp <= 0xFB4F:
        return "he"
    if unicodedata.category(ch).startswith("L"):
        return "en"
    return ""


class Measurer:
    """
    מודד טקסט בפיקסלים כפי ש-libass ירנדר אותו.

    כל תו נמדד בגופן ש-libass ישתמש בו בפועל: הגופן שנבחר כשהוא מכסה את
    התו, אחרת הגופן החלופי ש-fontconfig נותן לכתב הזה.
    """

    def __init__(self, family: str, weight: int, px: float) -> None:
        self.px = float(px)
        self.family = family
        self.weight = int(weight)
        self.primary = font_spec(family, self.weight)
        self._fallback: dict[str, FontSpec] = {}

    def _fallback_for(self, script: str) -> FontSpec:
        if script not in self._fallback:
            fam = fontsvc.fallback_family(self.family, self.weight, script)
            self._fallback[script] = font_spec(fam, self.weight)
        return self._fallback[script]

    def spec_for(self, ch: str) -> FontSpec:
        cp = ord(ch)
        if self.primary.covers(cp) is not False:
            return self.primary
        if unicodedata.category(ch) in ("Cf", "Mn"):     # סימני כיווניות וניקוד
            return self.primary
        return self._fallback_for("he" if _script_of(ch) == "he" else "en")

    def segments(self, text: str) -> list[tuple[FontSpec, str]]:
        out: list[tuple[FontSpec, str]] = []
        for ch in text:
            spec = self.spec_for(ch)
            if out and out[-1][0] is spec:
                out[-1] = (spec, out[-1][1] + ch)
            else:
                out.append((spec, ch))
        return out

    def width(self, text: str) -> float:
        if not text:
            return 0.0
        total = 0.0
        for spec, run in self.segments(text):
            if spec.file is None:
                total += len(run) * self.px * 0.55
                continue
            font = _pil_font(spec.file, max(4, int(round(self.px * spec.pil_per_ass))))
            total += float(font.getlength(run))
        return total

    def extent(self, text: str) -> tuple[float, float]:
        """(ascent, descent) של שורה: המקסימום על הגופנים שבשימוש בה."""
        specs = [s for s, run in self.segments(text) if run.strip()] or [self.primary]
        asc = max(s.asc_ratio for s in specs) * self.px
        desc = max(1.0 - s.asc_ratio for s in specs) * self.px
        return asc, desc

    @property
    def line_height(self) -> float:
        return self.px


# --------------------------------------------------------------------------
# פריסה
# --------------------------------------------------------------------------
@dataclass
class Line:
    words: list[dict[str, Any]]
    width: float = 0.0

    @property
    def text(self) -> str:
        return " ".join(str(w.get("text", "")) for w in self.words)


@dataclass
class LaidCue:
    start: float
    end: float
    lines: list[Line]
    language: str = ""
    emphasis: set[int] = field(default_factory=set)   # אינדקסים במילים השטוחות
    scale: float = 1.0                                  # הקטנה למילה רחבה מדי
    timed: bool = True

    @property
    def words(self) -> list[dict[str, Any]]:
        return [w for ln in self.lines for w in ln.words]


@dataclass
class Geometry:
    width: int
    height: int
    vertical: bool
    font_px: int
    outline_px: float
    shadow_px: float
    margin_v: int
    margin_h: int
    max_text_width: float
    alignment: int
    pad_x: float = 0.0        # ריפוד הרקע (קופסה/פס) סביב הטקסט
    pad_y: float = 0.0
    radius: float = 0.0       # פינות מעוגלות לקופסה


def geometry(style: dict[str, Any] | SubtitleStyleV2, width: int, height: int) -> Geometry:
    st = style.to_dict() if isinstance(style, SubtitleStyleV2) else dict(style)
    vertical = width / max(1, height) < 1.2
    zone = SAFE_ZONE[vertical]
    font_px = max(8, int(round(float(st["size"]) / 100.0 * height)))
    ref = height / 1080.0
    outline_px = round(float(st["outline"]) * ref, 2)
    shadow_px = round(float(st["shadow"]) * ref, 2)
    margin_h = int(round(width * zone["side"]))
    pos = st["position"]
    pad_x = pad_y = radius = 0.0
    if st["background"] == "box":
        pad_x, pad_y = round(font_px * 0.38, 1), round(font_px * 0.14, 1)
        radius = round(font_px * 0.22, 1)
    elif st["background"] == "bar":
        pad_y = round(max(6.0, font_px * 0.3), 1)
    if pos == "middle":
        margin_v = 0
    else:
        safe = zone["top"] if pos == "top" else zone["bottom"]
        # הרקע, המתאר והצל נכנסים לחישוב: כל מה שמצויר – לא רק הטקסט –
        # נשאר בתוך האזור הבטוח (הצל נופל למטה, ולכן רק בתחתית)
        ink = outline_px + (shadow_px if pos == "bottom" else 0.0)
        margin_v = int(math.ceil(max(height * float(st["offset"]) / 100.0, height * safe)
                                 + pad_y + ink))
    extra = 2 * (outline_px + pad_x) + shadow_px
    anim = ANIM_SCALE.get(st["animation"], 1.0)
    max_w = max(40.0, (width - 2 * margin_h - extra) / (1.0 + (anim - 1.0) * 0.6))
    return Geometry(width=width, height=height, vertical=vertical, font_px=font_px,
                    outline_px=outline_px, shadow_px=shadow_px, margin_v=margin_v,
                    margin_h=margin_h, max_text_width=max_w,
                    alignment={"top": 8, "middle": 5, "bottom": 2}[pos],
                    pad_x=pad_x, pad_y=pad_y, radius=radius)


def _display(token: str, style: dict[str, Any]) -> str:
    if style.get("uppercase") and not any(_script_of(c) == "he" for c in token):
        return token.upper()
    return token


def _cue_words(cue: Cue) -> tuple[list[dict[str, Any]], bool]:
    """מילים עם תזמון. בלי תזמון ברמת מילה – חלוקה יחסית לאורך."""
    words = [dict(w) for w in (cue.words or []) if str(w.get("text", "")).strip()]
    if words:
        return words, True
    tokens = (cue.text or "").split()
    if not tokens:
        return [], False
    total = sum(len(t) + 1 for t in tokens)
    dur = max(0.05, cue.end - cue.start)
    out, acc = [], 0
    for t in tokens:
        s = cue.start + dur * acc / total
        acc += len(t) + 1
        out.append({"start": s, "end": cue.start + dur * acc / total, "text": t})
    return out, False


_STRONG_END = re.compile(r"[.!?…׃]+[\"'”״»)\]]*$")
_WEAK_END = re.compile(r"[,;:—–]+[\"'”״»)\]]*$")

# עלויות (ביחידות של „רוחב שורה מלא בריבוע", כמו עלות חוסר האיזון)
HANGING_COST = 0.08       # מילית קישור בסוף שורה
STRONG_BONUS = 0.06       # שבירה אחרי סוף משפט
WEAK_BONUS = 0.03         # שבירה אחרי פסיק
GROUP_WEIGHT = 2.0        # מעבר בין כתוביות חשוב יותר ממעבר שורה
MIN_CUE_SECONDS = 0.7     # כתובית קצרה מזה „מהבהבת"
SHORT_COST = 0.3          # לכל שנייה חסרה


def _boundary_cost(token: str, packs: Sequence[Any]) -> float:
    """עלות שבירה אחרי `token`: מילית קישור יקרה, סוף משפט זול."""
    tok = (token or "").strip()
    cost = 0.0
    if any(p.is_hanging(tok) for p in packs):
        cost += HANGING_COST
    if _STRONG_END.search(tok):
        cost -= STRONG_BONUS
    elif _WEAK_END.search(tok):
        cost -= WEAK_BONUS
    return cost


def _span_seconds(words: Sequence[dict[str, Any]]) -> float:
    if not words:
        return 0.0
    s = float(words[0].get("start", 0.0))
    e = float(words[-1].get("end", words[-1].get("start", s)))
    return max(0.0, e - s)


def _short_cost(words: Sequence[dict[str, Any]], timed: bool) -> float:
    if not timed:
        return 0.0
    return max(0.0, MIN_CUE_SECONDS - _span_seconds(words)) * SHORT_COST


def _break_lines(words: list[dict[str, Any]], measurer: Measurer, style: dict[str, Any],
                 max_w: float, packs: Sequence[Any], *, timed: bool = True) -> list[Line]:
    """
    שבירה מאוזנת לשורות.

    מספר השורות הוא המינימום האפשרי (ככה פחות כתוביות מתפצלות), ובתוכו
    נבחרת החלוקה עם השורות הכי שוות באורכן – בלי לסיים שורה במילית
    קישור („של", „the") ועם העדפה לשבור אחרי סימן פיסוק. כששורה היא
    כתובית שלמה (`max_lines` = 1) נמנעות גם שורות קצרות מדי בזמן.
    """
    per_line = int(style["words_per_line"])
    one_line_cues = int(style["max_lines"]) == 1
    n = len(words)
    if n == 0:
        return []
    texts = [_display(str(w["text"]), style) for w in words]
    widths = [measurer.width(t) for t in texts]
    space = measurer.width(" ")
    prefix = [0.0]
    for w in widths:
        prefix.append(prefix[-1] + w)

    def line_w(i: int, j: int) -> float:
        return prefix[j] - prefix[i] + space * (j - i - 1)

    def feasible(i: int, j: int) -> bool:
        return j - i == 1 or (j - i <= per_line and line_w(i, j) <= max_w)

    # מינימום שורות – חמדני (אופטימלי למגבלות מונוטוניות כאלה)
    count, i = 0, 0
    while i < n:
        j = i + 1
        while j < n and feasible(i, j + 1):
            j += 1
        count, i = count + 1, j

    breaks = [_boundary_cost(str(words[j]["text"]), packs) for j in range(n)]
    if one_line_cues:
        breaks = [b * GROUP_WEIGHT for b in breaks]
    target = (prefix[n] + space * (n - count)) / count
    inf = float("inf")
    # best[k][j]: עלות מינימלית ל-j המילים הראשונות ב-k שורות
    best = [[inf] * (n + 1) for _ in range(count + 1)]
    back = [[-1] * (n + 1) for _ in range(count + 1)]
    best[0][0] = 0.0
    for k in range(1, count + 1):
        for j in range(k, n + 1):
            for i in range(max(k - 1, j - per_line), j):
                if best[k - 1][i] == inf or not feasible(i, j):
                    continue
                dev = (line_w(i, j) - target) / max(1.0, max_w)
                c = best[k - 1][i] + dev * dev
                if j < n:
                    c += breaks[j - 1]
                if one_line_cues:
                    c += _short_cost(words[i:j], timed)
                if c < best[k][j]:
                    best[k][j], back[k][j] = c, i
    if best[count][n] == inf:          # לא אמור לקרות: החמדני מצא פתרון
        return [Line(words=list(words), width=line_w(0, n))]
    cuts, j = [], n
    for k in range(count, 0, -1):
        i = back[k][j]
        cuts.append((i, j))
        j = i
    return [Line(words=list(words[i:j]), width=line_w(i, j)) for i, j in reversed(cuts)]


def _group_lines(lines: list[Line], max_lines: int, packs: Sequence[Any], *,
                 timed: bool = True) -> list[list[Line]]:
    """
    קיבוץ השורות לכתוביות של עד `max_lines` שורות.

    מספר הכתוביות מינימלי; המעבר ביניהן מועדף בסוף משפט, לא אחרי
    מילית קישור, ובלי כתובית שמוצגת פחות מ-`MIN_CUE_SECONDS`.
    """
    total = len(lines)
    if total <= max_lines:
        return [lines]
    groups = -(-total // max_lines)
    inf = float("inf")
    best = [[inf] * (total + 1) for _ in range(groups + 1)]
    back = [[-1] * (total + 1) for _ in range(groups + 1)]
    best[0][0] = 0.0
    for k in range(1, groups + 1):
        for j in range(k, total + 1):
            for i in range(max(k - 1, j - max_lines), j):
                if best[k - 1][i] == inf:
                    continue
                words = [w for ln in lines[i:j] for w in ln.words]
                c = best[k - 1][i] + _short_cost(words, timed)
                if j < total:
                    c += GROUP_WEIGHT * _boundary_cost(str(lines[j - 1].words[-1]["text"]),
                                                       packs)
                if c < best[k][j]:
                    best[k][j], back[k][j] = c, i
    cuts, j = [], total
    for k in range(groups, 0, -1):
        i = back[k][j]
        cuts.append((i, j))
        j = i
    return [lines[i:j] for i, j in reversed(cuts)]


def layout_cues(cues: Sequence[Cue], style: dict[str, Any] | SubtitleStyleV2, *,
                width: int, height: int, language: str = "") -> tuple[list[LaidCue], Geometry]:
    """
    פריסת הכתוביות לשורות מדודות, ופיצול לכתוביות נוספות כשיש יותר
    שורות מ-`max_lines`. מחזיר גם את הגיאומטריה בפיקסלים.
    """
    st = style.to_dict() if isinstance(style, SubtitleStyleV2) else clamp_style(style)
    geo = geometry(st, width, height)
    measurer = Measurer(st["font"], int(st["weight"]), geo.font_px)
    max_lines = int(st["max_lines"])
    out: list[LaidCue] = []
    for cue in cues:
        words, timed = _cue_words(cue)
        if not words:
            continue
        lang = cue.language or language or (_lang.detect_language(cue.text) or "")
        packs = _lang.packs_for(lang)
        lines = _break_lines(words, measurer, st, geo.max_text_width, packs, timed=timed)
        groups = _group_lines(lines, max_lines, packs, timed=timed)
        emph_all = set(cue.emphasis or [])
        offset = 0
        for gi, group in enumerate(groups):
            gwords = [w for ln in group for w in ln.words]
            start = cue.start if gi == 0 else float(gwords[0].get("start", cue.start))
            if gi == len(groups) - 1:
                end = cue.end
            else:
                end = float(groups[gi + 1][0].words[0].get("start", cue.end))
            widest = max(ln.width for ln in group)
            scale = min(1.0, geo.max_text_width / widest) if widest > 0 else 1.0
            emph = {i - offset for i in emph_all if offset <= i < offset + len(gwords)}
            out.append(LaidCue(start=max(0.0, start), end=max(start + 0.12, end),
                               lines=group, language=lang, emphasis=emph,
                               scale=round(scale, 4), timed=timed))
            offset += len(gwords)
    # בלי חפיפות
    out.sort(key=lambda c: c.start)
    for a, b in zip(out, out[1:]):
        if a.end > b.start:
            a.end = max(a.start + 0.1, b.start - 0.01)
    return out, geo


# --------------------------------------------------------------------------
# כתיבת ASS
# --------------------------------------------------------------------------
def _alpha_hex(opacity: float) -> int:
    return int(round(255 * (1.0 - max(0.0, min(1.0, opacity)))))


def _ass_color(hex_color: str, opacity: float = 1.0) -> str:
    return hex_to_ass_color(hex_color, alpha=_alpha_hex(opacity))


def _tag_color(hex_color: str) -> str:
    # בתגית \c אין ערוץ אלפא: &HBBGGRR&
    return hex_to_ass_color(hex_color)[4:]


def _header(st: dict[str, Any], geo: Geometry) -> str:
    primary = _ass_color(st["color"])
    secondary = _ass_color(st["highlight_color"])
    outline_c = _ass_color(st["outline_color"])
    back = _ass_color(st["shadow_color"], st["shadow_opacity"])
    bold_field = -1 if int(st["weight"]) >= 650 else 0
    title_size = int(round(geo.font_px * 1.25))
    fields = (f"{st['font']},{geo.font_px},{primary},{secondary},{outline_c},{back},"
              f"{bold_field},0,0,0,100,100,0,0,1,{geo.outline_px:.2f},"
              f"{geo.shadow_px:.2f},{geo.alignment},{geo.margin_h},{geo.margin_h},"
              f"{geo.margin_v},-1")
    title = (f"{st['font']},{title_size},{primary},{primary},{outline_c},{back},"
             f"-1,0,0,0,100,100,0,0,1,{max(geo.outline_px, 2.0):.2f},"
             f"{geo.shadow_px:.2f},5,{geo.margin_h},{geo.margin_h},0,-1")
    return (
        "[Script Info]\n; Polixor subtitles v2\nScriptType: v4.00+\nWrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n"
        f"PlayResX: {geo.width}\nPlayResY: {geo.height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Polixor,{fields}\n"
        f"Style: PolixorTitle,{title}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _dialogue(start: float, end: float, text: str, *, layer: int = 1,
              style: str = "Polixor") -> str:
    return (f"Dialogue: {layer},{format_timestamp(start, style='ass')},"
            f"{format_timestamp(end, style='ass')},{style},,0,0,0,,{text}")


def _mark(lang: str) -> str:
    pack = _lang.get_pack(lang)
    if pack is not None and pack.direction == "rtl":
        return RLM
    return LRM


def _anim_tag(anim: str, base: float) -> str:
    """תגיות המילה הפעילה: הגדלה קצרה וחזרה לגודל הבסיס של הכתובית."""
    b = base * 100.0
    if anim == "pop":
        s = b * ANIM_SCALE["pop"]
        return f"\\fscx{s:.1f}\\fscy{s:.1f}\\t(0,140,\\fscx{b:.1f}\\fscy{b:.1f})"
    if anim == "bounce":
        s, mid = b * ANIM_SCALE["bounce"], b * 1.06
        return (f"\\fscx{s:.1f}\\fscy{s:.1f}\\t(0,90,\\fscx{mid:.1f}\\fscy{mid:.1f})"
                f"\\t(90,200,\\fscx{b:.1f}\\fscy{b:.1f})")
    return ""


def _body(lc: LaidCue, st: dict[str, Any], *, active: Optional[int],
          visible_upto: Optional[int] = None) -> str:
    """
    טקסט האירוע: שורות מסומנות כיווניות, מילה פעילה/מודגשת/מוסתרת.

    מילים שעוד לא נאמרו (אנימציית word) שקופות אבל תופסות מקום, כך
    שהשורה לא זזה כשמילה מופיעה. הרקע הוא אירוע נפרד ולכן נשאר בגודלו
    המלא.
    """
    primary = _tag_color(st["color"])
    highlight = _tag_color(st["highlight_color"])
    anim = _anim_tag(st["animation"], lc.scale)
    base = lc.scale * 100.0
    reset_scale = f"\\fscx{base:.1f}\\fscy{base:.1f}" if anim else ""
    hide = "{\\alpha&HFF&}"
    mark = _mark(lc.language)
    out_lines: list[str] = []
    idx = 0
    hidden = False
    for ln in lc.lines:
        parts: list[str] = []
        for w in ln.words:
            tok = ass_escape(_display(str(w.get("text", "")).strip(), st))
            if visible_upto is not None and idx > visible_upto:
                # מכאן והלאה הכול מוסתר – התגית נשארת בתוקף עד סוף האירוע
                parts.append((hide if not hidden else "") + tok)
                hidden = True
            elif active is not None and idx == active:
                parts.append(f"{{\\c&H{highlight}&{anim}}}{tok}"
                             f"{{\\c&H{primary}&{reset_scale}}}")
            elif idx in lc.emphasis:
                parts.append(f"{{\\c&H{highlight}&}}{tok}{{\\c&H{primary}&}}")
            else:
                parts.append(tok)
            idx += 1
        out_lines.append(mark + " ".join(parts))
    prefix = f"{{\\b{int(st['weight'])}}}"
    if lc.scale < 0.999:
        prefix += f"{{\\fscx{base:.1f}\\fscy{base:.1f}}}"
    return prefix + "\\N".join(out_lines)


@dataclass
class Block:
    """מלבן הטקסט כפי ש-libass פורס אותו (לפני ריפוד הרקע)."""

    left: float
    top: float
    right: float
    bottom: float


def text_block(lc: LaidCue, geo: Geometry, measurer: Measurer) -> Block:
    """
    היכן libass ימקם את הכתובית: גובה כל שורה הוא ascent+descent של
    הגופנים שבשימוש בה (libass מנרמל כל גופן כך שסכומם = Fontsize),
    והבלוק מיושר לפי ה-Alignment וה-MarginV של הסגנון.
    """
    heights = []
    for ln in lc.lines:
        asc, desc = measurer.extent(ln.text)
        heights.append((asc + desc) * lc.scale)
    total = sum(heights)
    widest = max((ln.width for ln in lc.lines), default=0.0) * lc.scale
    if geo.alignment == 2:
        top = geo.height - geo.margin_v - total
    elif geo.alignment == 8:
        top = float(geo.margin_v)
    else:
        top = (geo.height - total) / 2.0
    left = (geo.width - widest) / 2.0
    return Block(left=left, top=top, right=left + widest, bottom=top + total)


def _rounded_rect(w: float, h: float, r: float) -> str:
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    if r < 0.5:
        return f"m 0 0 l {w:.1f} 0 {w:.1f} {h:.1f} 0 {h:.1f}"
    return (f"m {r:.1f} 0 l {w - r:.1f} 0 b {w:.1f} 0 {w:.1f} 0 {w:.1f} {r:.1f} "
            f"l {w:.1f} {h - r:.1f} b {w:.1f} {h:.1f} {w:.1f} {h:.1f} {w - r:.1f} {h:.1f} "
            f"l {r:.1f} {h:.1f} b 0 {h:.1f} 0 {h:.1f} 0 {h - r:.1f} "
            f"l 0 {r:.1f} b 0 0 0 0 {r:.1f} 0")


def _background_event(lc: LaidCue, st: dict[str, Any], geo: Geometry,
                      measurer: Measurer) -> Optional[str]:
    """
    רקע הכתובית כציור וקטורי בשכבה 0: קופסה אחת סביב כל השורות, או פס
    ברוחב מלא. ציור אחד לכל כתובית – בלי חפיפות שמכהות את הרקע
    (כמו ב-BorderStyle 3 של libass), ובלי תלות בגרסת libass.
    """
    if st["background"] not in ("box", "bar"):
        return None
    blk = text_block(lc, geo, measurer)
    top = max(0.0, blk.top - geo.pad_y)
    bottom = min(float(geo.height), blk.bottom + geo.pad_y)
    if st["background"] == "bar":
        left, right, radius = 0.0, float(geo.width), 0.0
    else:
        left = max(0.0, blk.left - geo.pad_x - geo.outline_px)
        right = min(float(geo.width), blk.right + geo.pad_x + geo.outline_px)
        radius = geo.radius
    shape = _rounded_rect(right - left, bottom - top, radius)
    color = _tag_color(st["background_color"])
    alpha = f"{_alpha_hex(st['background_opacity']):02X}"
    fade = "\\fad(160,160)" if st["animation"] == "fade" else ""
    return _dialogue(lc.start, lc.end,
                     f"{{\\an7\\pos({left:.1f},{top:.1f})\\bord0\\shad0\\c&H{color}&"
                     f"\\1a&H{alpha}&{fade}\\p1}}{shape}{{\\p0}}", layer=0)


def build_events(laid: Sequence[LaidCue], st: dict[str, Any], geo: Geometry,
                 measurer: Measurer) -> list[str]:
    anim = st["animation"]
    events: list[str] = []
    for lc in laid:
        bg = _background_event(lc, st, geo, measurer)
        if bg:
            events.append(bg)
        words = lc.words
        per_word = anim in ("karaoke", "word", "pop", "bounce") and lc.timed and len(words) > 1
        if not per_word:
            tag = "{\\fad(160,160)}" if anim == "fade" else ""
            events.append(_dialogue(lc.start, lc.end, tag + _body(lc, st, active=None)))
            continue
        first = float(words[0].get("start", lc.start))
        if first - lc.start > 0.05 and anim != "word":
            events.append(_dialogue(lc.start, first, _body(lc, st, active=None)))
        for i, w in enumerate(words):
            s = max(lc.start, float(w.get("start", lc.start)))
            if i == 0 and anim == "word":
                s = lc.start          # במצב word אין אירוע „לפני המילה הראשונה"
            e = float(words[i + 1].get("start", lc.end)) if i + 1 < len(words) else lc.end
            e = max(s + 0.04, min(e, lc.end))
            if s >= lc.end:
                break
            if anim == "word":
                body = _body(lc, st, active=None, visible_upto=i)
            else:
                body = _body(lc, st, active=i)
            events.append(_dialogue(s, e, body))
    return events


def write_ass_v2(cues: Sequence[Cue], dst: str | Path, *, width: int, height: int,
                 style: dict[str, Any] | SubtitleStyleV2, language: str = "",
                 title_text: str = "", title_seconds: float = 2.6) -> Path:
    """כותב קובץ ASS לצריבה, לפי סגנון v2 ולפי גודל הפריים בפועל."""
    st = style.to_dict() if isinstance(style, SubtitleStyleV2) else clamp_style(style)
    laid, geo = layout_cues(cues, st, width=width, height=height, language=language)
    measurer = Measurer(st["font"], int(st["weight"]), geo.font_px)
    events = build_events(laid, st, geo, measurer)
    if title_text.strip():
        lang = language or (_lang.detect_language(title_text) or "")
        events.insert(0, _dialogue(0.0, max(1.0, title_seconds),
                                   "{\\fad(350,350)}" + _mark(lang)
                                   + ass_escape(title_text.strip()),
                                   style="PolixorTitle"))
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(_header(st, geo) + "\n".join(events) + "\n", encoding="utf-8")
    return dst


def write_ass_v2_parts(cues: Sequence[Cue], prefix: Path, *, edit_plans: list,
                       width: int, height: int, style: dict[str, Any] | SubtitleStyleV2,
                       language: str = "", title_text: str = "") -> list[Optional[Path]]:
    """ASS לכל חלק בקליפ מרובה חלקים, בזמנים יחסיים לתחילת החלק."""
    from .subtitles import split_cues_for_parts

    durations = [float(p.out_duration) if p is not None else 0.0 for p in edit_plans]
    paths: list[Optional[Path]] = []
    for i, part in enumerate(split_cues_for_parts(list(cues), durations)):
        title = title_text if i == 0 else ""
        if not part and not title:
            paths.append(None)
            continue
        paths.append(write_ass_v2(part, Path(f"{prefix}{i:03d}.ass"), width=width,
                                  height=height, style=style, language=language,
                                  title_text=title))
    return paths


def base_cues(transcript, *, clip_start: float, clip_end: float,
              style: dict[str, Any]) -> list[Cue]:
    """
    כתוביות בסיס מהתמלול, לפני הפריסה: קיבוץ לפי פיסוק ושתיקות, בגודל
    שמתאים ל-`words_per_line * max_lines` מילים לכתובית.
    """
    from . import caption_engine as ce

    words_per_cue = max(1, int(style["words_per_line"]) * int(style["max_lines"]))
    base = ce.get_preset("clean")
    preset = ce.CaptionPreset(**{**base.__dict__, "name": "v2", "max_words": words_per_cue,
                                 "max_chars": 200, "max_lines": 1,
                                 "max_cue_seconds": max(1.6, 0.42 * words_per_cue + 1.0),
                                 "pause_break": 0.6})
    return ce.build_captions(transcript, clip_start=clip_start, clip_end=clip_end,
                             preset=preset, frame_chars=200)


def style_margin_v(style: dict[str, Any], width: int, height: int) -> int:
    return geometry(clamp_style(style), width, height).margin_v
