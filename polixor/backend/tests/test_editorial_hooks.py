"""
בדיקות לוו העריכתי (clip_intel/editorial) ולציור שלו על המסך (hook_overlay).

  * מועמדים מהקליפ: שאלה אמיתית/עמדה/ויכוח, מקוצרים ונקיים, נאמנים לטקסט.
  * קליקבייט גנרי נפסל; וו ממודל שפה בלי ראיה או עם עובדות שלא נאמרו נפסל;
    פרפרזה נאמנה מתקבלת; בלי מודל שפה – הכל עובד.
  * הציור: עד שתי שורות, באזור הבטוח, לא על רצועת הכתוביות, לא על פאנל
    המצלמה; נצרב בפועל לווידאו (בדיקת פיקסלים) ונעלם אחרי כמה שניות.

הרצה:  python3 tests/test_editorial_hooks.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxhooks_"))

from polixor.services import hook_overlay as ho                      # noqa: E402
from polixor.services.clip_intel import editorial as ed              # noqa: E402
from polixor.services.subtitle_style import clamp_style              # noqa: E402
from test_clip_quality import DEBATE, PODCAST, run                   # noqa: E402

CLIP = ("למה דווקא את החלוץ הזה הכניסו להרכב? לדעתי הוא לא מעניין, הייתי מחליף אותו בקשר הצעיר. "
        "מה פתאום, אני לא מסכים, הוא יותר טוב מכל הקשרים.")


def _with_model(reply, fn):
    from polixor.services import llm

    orig = llm.is_llm_enabled, llm.call_model
    llm.is_llm_enabled = lambda s: True
    llm.call_model = lambda system, user, s: reply(system, user) if callable(reply) else reply
    try:
        return fn()
    finally:
        llm.is_llm_enabled, llm.call_model = orig


# --------------------------------------------------------------------------
# מועמדים ודירוג
# --------------------------------------------------------------------------
def test_hook_is_the_real_question_and_faithful():
    res, tr = run(PODCAST, [(131.0, 133.0, 0.9)], short_min_seconds=15, short_max_seconds=45)
    e = res.review["selected"][0]["editorial"]
    assert e["hook"] == "האם בכלל שווה לפתוח פודקאסט היום?", e
    spoken = " ".join(s.text for s in tr.segments)
    assert ed.faithfulness(e["hook"], spoken, "he")[0]
    assert e["title"] and not e["title"].endswith("?") and e["title"] != e["hook"]
    assert "אחי" not in e["hook"], "vocatives are trimmed from the on-screen hook"


def test_trivial_question_is_not_the_hook():
    lines = [(10.0, 12.0, "איזה יום היום? רביעי נכון?"),
             (12.5, 16.0, "לדעתי הקבוצה הזאת הכי גרועה בליגה השנה"),
             (16.5, 21.0, "אין מצב שהם עולים לגמר עם ההגנה הזאת"),
             (21.5, 26.0, "בסופו של דבר הם ירדו ליגה, אין מה לדבר"),
             (32.0, 36.0, "טוב נמשיך לשחק את המשחק שלנו")]
    res, _ = run(lines)
    hooks = [r["editorial"]["hook"] for r in res.review["selected"]]
    assert hooks and all("איזה יום" not in h for h in hooks), hooks


def test_generic_clickbait_is_rejected_unless_said():
    for bad in ("אתם חייבים לראות את זה", "לא תאמינו מה קרה", "רגע מטורף בלייב",
                "מה אתם חושבים?", "זה פשוט הזוי"):
        c = ed.check(ed.HookCandidate(text=bad, source="llm", evidence="למה דווקא את החלוץ הזה"),
                     CLIP, "he")
        assert c.rejected, bad
    said = "אתם חייבים לראות את החלוץ הזה משחק"
    c = ed.check(ed.HookCandidate(text="אתם חייבים לראות את החלוץ הזה", source="llm",
                                  evidence="אתם חייבים לראות"), said, "he")
    assert c.rejected != "generic_clickbait", c.rejected


def test_unsupported_hook_text_is_rejected():
    # עובדה שלא נאמרה
    c = ed.check(ed.HookCandidate(text="החלוץ עוזב את הקבוצה מחר בבוקר", source="llm",
                                  evidence="למה דווקא את החלוץ הזה"), CLIP, "he")
    assert c.rejected == "ungrounded_words", c.rejected
    # ציטוט ראיה שלא מופיע בקליפ
    c = ed.check(ed.HookCandidate(text="למה דווקא החלוץ הזה?", source="llm",
                                  evidence="הוא אמר שהוא שונא את המאמן"), CLIP, "he")
    assert c.rejected == "unsupported_evidence", c.rejected
    # מספר ושם שלא נאמרו
    c = ed.check(ed.HookCandidate(text="החלוץ הזה שווה 50 מיליון", source="llm",
                                  evidence="למה דווקא את החלוץ הזה"), CLIP, "he")
    assert c.rejected, c.rejected


def test_faithful_paraphrase_from_model_is_accepted_and_ranked():
    reply = json.dumps({"hooks": [{"text": "הדעה הזאת על החלוץ תעצבן אוהדים",
                                   "evidence": "לדעתי הוא לא מעניין"},
                                  {"text": "לא תאמינו מה קרה", "evidence": "מה פתאום"}],
                        "titles": [{"text": "למה הקשר הצעיר עדיף על החלוץ",
                                    "evidence": "הקשר הצעיר עדיף עליו"}]}, ensure_ascii=False)
    res, _ = _with_model(reply, lambda: run(DEBATE))
    e = res.review["selected"][0]["editorial"]
    texts = {c["text"]: c for c in e["candidates"]}
    assert "הדעה הזאת על החלוץ תעצבן אוהדים" in texts, e
    assert any(r["text"] == "לא תאמינו מה קרה" and r["rejected"] == "generic_clickbait"
               for r in e["rejected"]), e["rejected"]


def test_works_without_a_model_and_when_the_model_fails():
    from polixor.services import llm
    plain, _ = run(DEBATE)

    def boom(system, user):
        raise llm.AiProviderError(message_key="processing.llm.no_model_mode")
    failing, _ = _with_model(boom, lambda: run(DEBATE))
    a = plain.review["selected"][0]["editorial"]
    b = failing.review["selected"][0]["editorial"]
    assert a["hook"] and a["hook"] == b["hook"]


def test_hook_rides_on_the_candidate_for_rendering():
    res, _ = run(DEBATE)
    q = res.selected[0].quality
    assert q["editorial"]["hook"] and len(q["editorial"]["hook"].split()) <= ed.MAX_WORDS


# --------------------------------------------------------------------------
# ציור
# --------------------------------------------------------------------------
V2 = clamp_style({"version": 2})


def test_overlay_is_in_the_safe_area_and_off_the_subtitles():
    zone = ho.subtitle_zone(V2, v2=True, width=1080, height=1920)
    pl = ho.layout("האם בכלל שווה לפתוח פודקאסט היום?", width=1080, height=1920,
                   font="DejaVu Sans", plan=None, subtitle_zone=zone)
    assert pl is not None and len(pl.lines) <= 2
    x0, y0, x1, y1 = pl.rect(1080, 1920)
    assert y0 >= ho.TOP_SAFE[True] - 1e-6 and x0 >= 0.05 and x1 <= 0.95
    assert y1 <= zone[0] or y0 >= zone[1], (pl.rect(1080, 1920), zone)
    # subtitles at the top: the hook moves elsewhere, never on them
    top = clamp_style({"version": 2, "position": "top"})
    zt = ho.subtitle_zone(top, v2=True, width=1080, height=1920)
    pt = ho.layout("האם בכלל שווה לפתוח פודקאסט?", width=1080, height=1920, font="DejaVu Sans",
                   plan=None, subtitle_zone=zt)
    assert pt is None or pt.rect(1080, 1920)[1] >= zt[1] or pt.rect(1080, 1920)[3] <= zt[0]


def test_overlay_does_not_cover_the_face_panel():
    for order in ("content_top", "cam_top"):
        plan = NS(layout="reaction", pieces=[NS(start=0.0, end=40.0, kind="reaction", cam=object(),
                                               content_frac=0.55, order=order)])
        pl = ho.layout("למה דווקא את החלוץ הזה הכניסו להרכב?", width=1080, height=1920,
                       font="DejaVu Sans", plan=plan,
                       subtitle_zone=ho.subtitle_zone(V2, v2=True, width=1080, height=1920))
        assert pl is not None and pl.zone == "seam", order
        r = pl.rect(1080, 1920)
        cam = ho.avoid_zones(plan, 3.0)[0][0]
        area = (r[2] - r[0]) * (r[3] - r[1])
        assert ho._overlap(r, cam) <= 0.35 * area + 1e-9, (order, r, cam)
    # פנים בפריים מלא – הוו לא על אזור הפנים
    face = NS(layout="face", pieces=[NS(start=0.0, end=40.0, kind="camera", cam=None,
                                        content_frac=1.0, order="content_top")])
    pl = ho.layout("הדעה הזאת תעצבן הרבה סטרימרים", width=1080, height=1920, font="DejaVu Sans",
                   plan=face, subtitle_zone=None)
    assert pl is not None
    r = pl.rect(1080, 1920)
    area = (r[2] - r[0]) * (r[3] - r[1])
    assert ho._overlap(r, ho.avoid_zones(face, 3)[0][0]) <= 0.12 * area + 1e-9, r


def test_weak_interjection_is_not_a_hook():
    c = ed.check(ed.HookCandidate(text="רגע רגע רגע", source="hook_words"), "רגע רגע רגע אין מצב", "he")
    assert c.rejected or c.score < ed.MIN_HOOK_SCORE, (c.rejected, c.score)


def test_too_long_hook_is_not_drawn():
    assert ho.layout(" ".join(["מילה"] * 30), width=1080, height=1920, font="DejaVu Sans") is None


def test_overlay_is_burned_into_the_video_and_disappears():
    from polixor.util.ffmpeg import ffmpeg_bin

    d = Path(tempfile.mkdtemp())
    src = d / "src.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=0x202830:s=540x960:d=6:r=25", "-pix_fmt", "yuv420p", str(src)], check=True)
    ass = d / "subs.ass"
    from polixor.services.subtitles import Cue
    from polixor.services.subtitle_render import write_ass_v2
    write_ass_v2([Cue(start=4.2, end=5.5, text="שלום לכולם", language="he")], ass,
                 width=540, height=960, style=V2, language="he")
    sub, _, rec = ho.apply_to_clip("האם בכלל שווה לפתוח פודקאסט היום?", sub_path=ass, parts=[],
                                   work_dir=d, clip_id="c", width=540, height=960, font="DejaVu Sans",
                                   plan=None, style=V2, v2=True, subtitles_on=True, language="he")
    assert rec["rendered"] and "PolixorHook" in ass.read_text("utf-8")
    out = d / "out.mp4"
    esc = str(sub).replace(":", "\\:")
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(src), "-vf", f"ass={esc}",
                    "-pix_fmt", "yuv420p", str(out)], check=True)

    def luma(t: float) -> float:
        x0, y0, x1, y1 = rec["rect"]
        w, h = 540, 960
        crop = f"crop={int((x1 - x0) * w * 0.6)}:{int((y1 - y0) * h * 0.4)}:" \
               f"{int((x0 + (x1 - x0) * 0.2) * w)}:{int((y0 + (y1 - y0) * 0.3) * h)}"
        raw = subprocess.run([ffmpeg_bin(), "-loglevel", "error", "-ss", str(t), "-i", str(out),
                              "-frames:v", "1", "-vf", crop + ",format=gray", "-f", "rawvideo", "-"],
                             check=True, capture_output=True).stdout
        return sum(raw) / max(1, len(raw))
    early, late = luma(1.0), luma(4.8)
    assert early > 150 and late < 80, (early, late)


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} בדיקות וו עריכתי עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
