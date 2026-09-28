"""
בדיקות למערכת הכתוביות v2 – ברינדור אמיתי דרך FFmpeg ו-libass.

מה נבדק (כל טענה נמדדת על פריים שרונדר, לא על קובץ ה-ASS בלבד):
  * כל פיקסל של הכתובית (כולל מתאר, צל ורקע) בתוך האזור הבטוח –
    ב-1080×1920, ‏1080×1080 ו-1920×1080, בעברית ובאנגלית, בכל preset.
  * `max_lines` ו-`words_per_line` נאכפים (גם בספירת שורות בפריים).
  * סדר המילים: ההדגשה הרצה זזה מימין לשמאל בעברית ומשמאל לימין באנגלית,
    גם בשורה עברית שמתחילה במילה לועזית.
  * סימן פיסוק בסוף שורה בעברית נמצא משמאל למילה.
  * הגודל לא משתנה בייצוא חוזר, ואין „כיווץ כפול".
  * רקע קופסה אחיד (בלי אזורים כהים מחפיפות) ומכיל את הטקסט.
  * קליפ מרובה חלקים: לכל חלק כתוביות בזמנים של החלק.
  * הכותב הישן: סדר מילים נכון בעברית (באג 4) ושורה אחת ב-preset של שורה
    אחת (באג 5).
  * תצוגה מקדימה: אותו כותב, פחות מ-400ms.

הרצה:  python3 tests/test_subtitle_style.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxsub2_"))

import numpy as np                                                  # noqa: E402
from PIL import Image                                               # noqa: E402

from polixor.config import PATHS                                    # noqa: E402

PATHS.ensure()

from polixor.services import subtitle_preview as sp                 # noqa: E402
from polixor.services import subtitle_render as sr                  # noqa: E402
from polixor.services import subtitle_style as ss                   # noqa: E402
from polixor.services import subtitles as legacy                    # noqa: E402
from polixor.services.render import escape_filter_path              # noqa: E402
from polixor.util.ffmpeg import ffmpeg_bin                          # noqa: E402

TMP = Path(os.environ["POLIXOR_DATA_DIR"]) / "subtests"
TMP.mkdir(parents=True, exist_ok=True)
BG = (48, 80, 160)                      # רקע אחיד לזיהוי פיקסלים של הכתובית
SIZES = [(1080, 1920), (1080, 1080), (1920, 1080)]

HE_TEXT = "שלום לכולם, היום אני מראה לכם את YouTube Studio החדש ואיך הוא עובד בפועל!"
EN_TEXT = "Hello everyone, today I am showing you the new YouTube Studio and how it really works!"


def cue(text: str, *, start: float = 0.0, per: float = 0.45, lang: str = ""
        ) -> legacy.Cue:
    words, t = [], start
    for tok in text.split():
        words.append({"start": round(t, 3), "end": round(t + per * 0.9, 3), "text": tok})
        t += per
    return legacy.Cue(start=start, end=round(t, 3), text=text, words=words, language=lang)


def render(ass: Path, w: int, h: int, t: float) -> np.ndarray:
    out = ass.with_suffix(f".{int(t * 1000)}.png")
    color = "0x{:02X}{:02X}{:02X}".format(*BG)
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:d=60",
                    "-ss", f"{t:.3f}", "-vf", f"ass='{escape_filter_path(ass)}'",
                    "-frames:v", "1", str(out)], check=True, capture_output=True)
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def ink(img: np.ndarray, tol: int = 24) -> np.ndarray:
    return (np.abs(img - np.array(BG)).max(axis=2) > tol)


def bbox(mask: np.ndarray):
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def highlight_x(img: np.ndarray, color=(255, 212, 0)) -> float | None:
    """מרכז אופקי של פיקסלים בצבע ההדגשה (ברירת מחדל: צהוב של clean)."""
    diff = np.abs(img - np.array(color)).max(axis=2)
    cols = np.flatnonzero((diff < 60).any(axis=0))
    return float(cols.mean()) if cols.size else None


def text_rows(mask: np.ndarray, min_gap: int) -> int:
    """מספר שורות טקסט: רצפי שורות-פיקסלים עם דיו, מופרדים ברווח."""
    rows = mask.any(axis=1)
    count, gap, inside = 0, min_gap, False
    for r in rows:
        if r:
            if not inside and gap >= min_gap:
                count += 1
            inside, gap = True, 0
        else:
            inside = False
            gap += 1
    return count


def style(preset: str, lang: str, **over) -> dict:
    st = ss.default_style(lang, preset=preset)
    st.update(over)
    return ss.clamp_style(st, language=lang)


# ==========================================================================
# מודל הסגנון
# ==========================================================================
def test_presets_have_labels_and_are_valid():
    cat = ss.preset_catalog("he")
    assert [p["id"] for p in cat] == ["clean", "viral", "cinematic", "podcast", "story"]
    for p in cat:
        assert p["label"]["he"] and p["label"]["en"], p["id"]
        assert p["description"]["he"] and p["description"]["en"], p["id"]
        # preset תקין הוא נקודת שבת של האימות
        assert ss.clamp_style(p["style"], language="he") == p["style"], p["id"]


def test_clamp_fixes_bad_values_and_font_injection():
    st = ss.clamp_style({"size": 99, "weight": 950, "color": "red", "outline": -3,
                         "background": "neon", "animation": "explode",
                         "words_per_line": 0, "max_lines": 9,
                         "font": "Arial,{\\pos(0,0)}Evil"}, language="en")
    assert st["size"] == ss.SIZE_RANGE[1]
    assert st["weight"] == 900
    assert st["color"] == "#FFFFFF"
    assert st["outline"] == 0.0
    assert st["background"] == "none" and st["animation"] == "karaoke"
    assert st["words_per_line"] == 1 and st["max_lines"] == 3
    # שם הגופן נכנס לשורת Style ב-ASS: אסור שיכיל פסיקים או סוגריים
    assert "," not in st["font"] and "{" not in st["font"] and "\\" not in st["font"]


def test_legacy_style_migrates_with_clip_height():
    old = {"font": "Noto Sans Hebrew", "size": 70, "primary_color": "#FFFFFF",
           "outline_color": "#000000", "outline": 3.0, "position": "bottom",
           "word_level": True, "animation": "pop", "bold": True, "margin_v": 150}
    v2_ref = ss.from_legacy(old, vertical=True, language="he")
    v2_720 = ss.from_legacy(old, vertical=True, language="he", ref_height=1280)
    assert abs(v2_ref["size"] - 70 / 1920 * 100) < 0.01, v2_ref["size"]
    # קליפ שרונדר ב-720×1280: 70px הם חלק גדול יותר מהפריים
    assert abs(v2_720["size"] - 70 / 1280 * 100) < 0.01, v2_720["size"]
    assert v2_720["animation"] == "pop" and ss.is_v2(v2_720)


def test_size_is_relative_and_stable_across_reexport():
    """אותו סגנון, ייצוא ואז ייצוא חוזר מהסגנון השמור: אותו Fontsize."""
    st = style("clean", "he")
    sizes = []
    for _ in range(3):
        ass = sr.write_ass_v2([cue(HE_TEXT, lang="he")], TMP / "stable.ass",
                              width=1080, height=1920, style=st, language="he")
        line = next(ln for ln in ass.read_text("utf-8").splitlines()
                    if ln.startswith("Style: Polixor,"))
        sizes.append(int(line.split(",")[2]))
        st = ss.clamp_style(dict(st), language="he")   # „נשמר ונטען מחדש"
    assert len(set(sizes)) == 1, sizes
    assert sizes[0] == round(st["size"] / 100 * 1920), sizes
    # אותו סגנון בפריים חצי גודל: חצי פיקסלים (יחסי לפריים, לא כיווץ כפול)
    g_full = sr.geometry(st, 1080, 1920)
    g_half = sr.geometry(st, 540, 960)
    assert abs(g_full.font_px / g_half.font_px - 2.0) < 0.05


# ==========================================================================
# פריסה
# ==========================================================================
def test_words_per_line_and_max_lines_everywhere():
    for preset in ss.PRESETS:
        for lang, text in (("he", HE_TEXT), ("en", EN_TEXT)):
            st = style(preset, lang)
            for w, h in SIZES:
                laid, geo = sr.layout_cues([cue(text, lang=lang)], st, width=w, height=h,
                                           language=lang)
                assert laid, (preset, lang, w, h)
                for lc in laid:
                    assert 1 <= len(lc.lines) <= st["max_lines"], (preset, lang, w, h)
                    for ln in lc.lines:
                        assert len(ln.words) <= st["words_per_line"], (preset, ln.text)
                        assert ln.width * lc.scale <= geo.max_text_width + 0.5, \
                            (preset, lang, w, h, ln.text, ln.width, geo.max_text_width)
                # כל המילים נשמרו, בסדר
                words = [wd["text"] for lc in laid for wd in lc.words]
                assert words == text.split(), (preset, lang)


def test_split_cues_are_contiguous_and_ordered():
    st = style("viral", "en")
    laid, _ = sr.layout_cues([cue(EN_TEXT, lang="en")], st, width=1080, height=1920,
                             language="en")
    assert len(laid) > 3
    for a, b in zip(laid, laid[1:]):
        assert a.end <= b.start + 1e-6, (a.end, b.start)
        assert b.start - a.end < 0.05, "רווח בין כתוביות מאותו משפט"


def test_no_hanging_word_at_line_end_when_avoidable():
    # 6 מילים, 3 למטה ולמעלה: „the" לא אמור לסיים שורה
    st = style("clean", "en", words_per_line=4, max_lines=2)
    laid, _ = sr.layout_cues([cue("we went to the new store", lang="en")], st,
                             width=1080, height=1920, language="en")
    for lc in laid:
        for ln in lc.lines[:-1]:
            assert ln.words[-1]["text"] not in ("the", "to"), [x.text for x in lc.lines]
    st_he = style("clean", "he", words_per_line=4, max_lines=2)
    laid, _ = sr.layout_cues([cue("הלכנו אתמול עם החברים של אחי לים", lang="he")], st_he,
                             width=1080, height=1920, language="he")
    for lc in laid:
        for ln in lc.lines[:-1]:
            assert ln.words[-1]["text"] not in ("עם", "של"), [x.text for x in lc.lines]


def test_overlong_word_is_scaled_not_clipped():
    st = style("viral", "en", size=11.0)
    laid, geo = sr.layout_cues([cue("Supercalifragilisticexpialidocious", lang="en")], st,
                               width=1080, height=1920, language="en")
    assert laid[0].scale < 1.0
    ass = sr.write_ass_v2([cue("Supercalifragilisticexpialidocious", lang="en")],
                          TMP / "long.ass", width=1080, height=1920, style=st, language="en")
    box = bbox(ink(render(ass, 1080, 1920, 0.2)))
    assert box is not None and box[0] > 0 and box[2] < 1079, box


# ==========================================================================
# רינדור: אזור בטוח ומספר שורות
# ==========================================================================
def test_rendered_text_stays_inside_safe_zone():
    failures = []
    for preset in ss.PRESETS:
        for lang, text in (("he", HE_TEXT), ("en", EN_TEXT)):
            st = style(preset, lang)
            for w, h in SIZES:
                c = cue(text, lang=lang)
                ass = sr.write_ass_v2([c], TMP / f"safe_{preset}_{lang}_{w}.ass",
                                      width=w, height=h, style=st, language=lang)
                # בתחילת מילה – כשאנימציית pop/bounce בשיא ההגדלה
                img = render(ass, w, h, c.words[2]["start"] + 0.02)
                box = bbox(ink(img))
                assert box is not None, (preset, lang, w, h)
                zone = sr.SAFE_ZONE[w / h < 1.2]
                x0, y0, x1, y1 = box
                ok = (x0 >= w * zone["side"] - 1 and x1 <= w * (1 - zone["side"]) + 1)
                if st["position"] == "bottom":
                    ok = ok and y1 <= h * (1 - zone["bottom"]) + 1
                if not ok:
                    failures.append((preset, lang, w, h, box))
    assert not failures, failures


def test_top_position_respects_top_safe_zone():
    st = style("clean", "en", position="top", offset=0)
    c = cue(EN_TEXT, lang="en")
    for w, h in SIZES:
        ass = sr.write_ass_v2([c], TMP / f"top_{w}.ass", width=w, height=h, style=st,
                              language="en")
        box = bbox(ink(render(ass, w, h, 0.5)))
        zone = sr.SAFE_ZONE[w / h < 1.2]
        assert box[1] >= h * zone["top"] - 1, (w, h, box)


def test_rendered_line_count_matches_max_lines():
    for preset, lang, text in (("clean", "he", HE_TEXT), ("viral", "en", EN_TEXT),
                               ("cinematic", "en", EN_TEXT)):
        st = style(preset, lang)
        for w, h in SIZES:
            c = cue(text, lang=lang)
            laid, geo = sr.layout_cues([c], st, width=w, height=h, language=lang)
            ass = sr.write_ass_v2([c], TMP / f"lines_{preset}_{w}.ass", width=w,
                                  height=h, style=st, language=lang)
            first = laid[0]
            t = (first.start + first.end) / 2
            n = text_rows(ink(render(ass, w, h, t)), min_gap=max(2, geo.font_px // 12))
            assert n == len(first.lines) <= st["max_lines"], (preset, w, h, n,
                                                              [x.text for x in first.lines])


# ==========================================================================
# רינדור: כיווניות
# ==========================================================================
def _highlight_positions(text: str, lang: str, preset: str = "clean", w=1920, h=1080):
    st = style(preset, lang, words_per_line=12, max_lines=1)
    c = cue(text, lang=lang, per=0.5)
    ass = sr.write_ass_v2([c], TMP / f"order_{lang}_{abs(hash(text))}.ass", width=w,
                          height=h, style=st, language=lang)
    return [highlight_x(render(ass, w, h, wd["start"] + 0.25)) for wd in c.words]


def test_karaoke_order_hebrew_rtl_english_ltr():
    he = _highlight_positions("זה עלה 250 שקל בלבד?!", "he")
    en = _highlight_positions("It cost only $250 today?!", "en")
    assert None not in he and None not in en, (he, en)
    assert all(a > b for a, b in zip(he, he[1:])), f"עברית לא מימין לשמאל: {he}"
    assert all(a < b for a, b in zip(en, en[1:])), f"אנגלית לא משמאל לימין: {en}"


def test_hebrew_line_starting_with_latin_word_stays_rtl():
    xs = _highlight_positions("YouTube זה הכלי הכי טוב", "he")
    assert None not in xs, xs
    # המילה הראשונה (לועזית) בקצה הימני, והשאר משמאלה לפי הסדר
    assert all(a > b for a, b in zip(xs, xs[1:])), xs


def _single_word_mask(word: str, lang: str) -> np.ndarray:
    st = style("clean", lang, animation="none")
    ass = sr.write_ass_v2([cue(word, lang=lang)], TMP / f"w_{abs(hash(word))}.ass",
                          width=1920, height=1080, style=st, language=lang)
    return ink(render(ass, 1920, 1080, 0.2))


def _punct_side(with_punct: str, bare: str, lang: str) -> str:
    """באיזה צד של המילה נמצא הפיסוק: משווים את המילה בלי הפיסוק לכל צד."""
    a, b = _single_word_mask(with_punct, lang), _single_word_mask(bare, lang)
    bx0, by0, bx1, by1 = bbox(b)
    ax0, _, ax1, _ = bbox(a)
    tb = b[by0:by1 + 1, bx0:bx1 + 1]
    wb = tb.shape[1]
    left = a[by0:by1 + 1, ax0:ax0 + wb]
    right = a[by0:by1 + 1, ax1 - wb + 1:ax1 + 1]
    d_left = np.abs(left.astype(int) - tb).sum()
    d_right = np.abs(right.astype(int) - tb).sum()
    return "left" if d_right < d_left else "right"


def test_rtl_punctuation_lands_on_the_left():
    assert _punct_side("בלבד?!", "בלבד", "he") == "left"
    assert _punct_side("האמנתי.", "האמנתי", "he") == "left"
    # ובאנגלית – מימין
    assert _punct_side("today?!", "today", "en") == "right"


# ==========================================================================
# רינדור: רקעים ואנימציות
# ==========================================================================
def test_box_background_is_uniform_and_contains_text():
    st = style("podcast", "he")
    c = cue(HE_TEXT, lang="he")
    ass = sr.write_ass_v2([c], TMP / "box.ass", width=1080, height=1920, style=st,
                          language="he")
    img = render(ass, 1080, 1920, c.words[3]["start"] + 0.1)
    mask = ink(img)
    x0, y0, x1, y1 = bbox(mask)
    inner = img[y0 + 6:y1 - 5, x0 + 6:x1 - 5].reshape(-1, 3)
    # פיקסלי רקע בתוך הקופסה = לא טקסט (לבן/צבע הדגשה/החלקה)
    bright = inner.max(axis=1) > 120
    box_px = inner[~bright]
    assert box_px.size > 0
    spread = np.percentile(box_px, 95, axis=0) - np.percentile(box_px, 5, axis=0)
    # BorderStyle 3 של libass היה יוצר כאן אזורים כהים כפולים בחפיפות
    assert spread.max() <= 12, f"קופסה לא אחידה: {spread}"
    # הטקסט (פיקסלים בהירים) כולו בתוך הקופסה, עם ריפוד
    text = (img.max(axis=2) > 200)
    tx0, ty0, tx1, ty1 = bbox(text)
    assert x0 < tx0 - 8 and tx1 + 8 < x1 and y0 < ty0 and ty1 < y1, \
        ((x0, y0, x1, y1), (tx0, ty0, tx1, ty1))


def test_bar_background_spans_full_width():
    st = style("clean", "en", background="bar", background_opacity=0.7)
    c = cue(EN_TEXT, lang="en")
    ass = sr.write_ass_v2([c], TMP / "bar.ass", width=1920, height=1080, style=st,
                          language="en")
    box = bbox(ink(render(ass, 1920, 1080, 0.5)))
    assert box[0] == 0 and box[2] == 1919, box


def test_word_animation_reveals_words_in_order():
    st = style("story", "he")
    c = cue("אחת שתיים שלוש ארבע", lang="he")
    ass = sr.write_ass_v2([c], TMP / "reveal.ass", width=1080, height=1920, style=st,
                          language="he")
    # כמות הפיקסלים המצוירים גדלה עם כל מילה שנאמרת (מילים עתידיות שקופות)
    areas = [int(ink(render(ass, 1080, 1920, wd["start"] + 0.2)).sum()) for wd in c.words]
    assert all(a < b for a, b in zip(areas, areas[1:])), areas
    # והפריסה לא זזה: המילה הראשונה באותו מקום לאורך כל הכתובית
    first = bbox(ink(render(ass, 1080, 1920, c.words[0]["start"] + 0.2)))
    last = bbox(ink(render(ass, 1080, 1920, c.words[-1]["start"] + 0.2)))
    assert first[1] >= last[1] and first[2] <= last[2] + 1, (first, last)


# ==========================================================================
# קליפ מרובה חלקים
# ==========================================================================
class _Plan:
    def __init__(self, d: float) -> None:
        self.out_duration = d


def test_multipart_clips_get_per_part_subtitles():
    cues = [cue("אחת שתיים", start=0.2, lang="he"), cue("שלוש ארבע", start=3.3, lang="he")]
    parts = sr.write_ass_v2_parts(cues, TMP / "mp_", edit_plans=[_Plan(3.0), _Plan(4.0)],
                                  width=1080, height=1920, style=style("clean", "he"),
                                  language="he")
    assert len(parts) == 2 and all(parts), parts
    p0 = parts[0].read_text("utf-8")
    p1 = parts[1].read_text("utf-8")
    assert "אחת" in p0 and "שלוש" not in p0
    # בחלק השני הזמנים מתחילים מאפס של החלק (3.3 - 3.0 = 0.3)
    assert "שלוש" in p1 and "0:00:00.30" in p1, p1[-400:]


# ==========================================================================
# הכותב הישן (משימות שנוצרו לפני v2)
# ==========================================================================
def test_legacy_writer_keeps_hebrew_word_order():
    st = legacy.SubtitleStyle(font="Noto Sans Hebrew", size=60, highlight_color="#FFD400",
                              margin_v=120, margin_h=80, word_level=True, animation="none")
    c = cue("זה עלה 250 שקל בלבד?!", per=0.5, lang="he")
    ass = TMP / "legacy.ass"
    legacy.write_ass([c], ass, width=1920, height=1080, style=st)
    xs = [highlight_x(render(ass, 1920, 1080, wd["start"] + 0.25)) for wd in c.words]
    assert None not in xs and all(a > b for a, b in zip(xs, xs[1:])), xs


def test_legacy_writer_one_line_preset():
    from polixor.services import caption_engine as ce

    base = legacy.SubtitleStyle(font="Noto Sans", size=60)
    st = ce.apply_preset(base, ce.get_preset("viral"), frame_w=1080, frame_h=1920,
                         vertical=True, already_scaled=True)
    assert st.max_lines == ce.get_preset("viral").max_lines == 1
    c = cue("one two three four five six seven", lang="en")
    ass = TMP / "legacy_one.ass"
    legacy.write_ass([c], ass, width=1080, height=1920, style=st)
    dialogue = [ln for ln in ass.read_text("utf-8").splitlines()
                if ln.startswith("Dialogue:")]
    assert dialogue and all("\\N" not in ln for ln in dialogue), dialogue[:2]


# ==========================================================================
# תצוגה מקדימה ו-API
# ==========================================================================
def test_preview_uses_same_layout_and_is_fast():
    st = style("clean", "he")
    sp.render_preview(st, width=1080, height=1920, language="he")      # חימום
    t0 = time.perf_counter()
    res = sp.render_preview(st, width=1080, height=1920, language="he", progress=0.1)
    elapsed = (time.perf_counter() - t0) * 1000
    assert elapsed < 400, f"{elapsed:.0f}ms"
    assert (res.width, res.height) == (360, 640)
    img = Image.open(__import__("io").BytesIO(res.image))
    assert img.size == (360, 640)
    laid, _ = sr.layout_cues([sp.sample_cue(sp.i18n.tr("subtitles.sample", "he"),
                                            language="he")],
                             st, width=1080, height=1920, language="he")
    shown = next(lc for lc in laid if lc.start <= res.at < lc.end)
    assert res.lines == [[ln.text for ln in shown.lines]]


def test_subtitle_api_routes():
    from fastapi.testclient import TestClient

    from polixor.main import app

    c = TestClient(app)
    r = c.get("/api/subtitles/presets", headers={"X-Polixor-Lang": "en"})
    assert r.status_code == 200 and len(r.json()["presets"]) == 5
    r = c.get("/api/subtitles/fonts")
    fonts = r.json()["fonts"]
    assert r.status_code == 200 and fonts and all("weights" in f for f in fonts)
    r = c.post("/api/subtitles/preview", json={"style": {"preset": "viral"},
                                               "aspect": "1:1", "language": "en"})
    body = r.json()
    assert r.status_code == 200 and body["image"].startswith("data:image/png;base64,")
    assert (body["target_width"], body["target_height"]) == (1080, 1080)
    r = c.post("/api/subtitles/preview", json={"aspect": "3:7"},
               headers={"X-Polixor-Lang": "en"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_aspect"
    assert "3:7" in r.json()["detail"]["message"]


# ==========================================================================
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        t0 = time.perf_counter()
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name} ({time.perf_counter() - t0:.1f}s)")
            passed += 1
        except AssertionError as exc:
            print(f"  \033[91m✗\033[0m {name}: {exc}")
            failed.append(name)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} בדיקות כתוביות v2 עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
