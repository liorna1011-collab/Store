"""
מנוע העריכה – מה שהופך חיתוך גולמי לקליפ ערוך.

במקום לחתוך קטע רציף אחד, המנוע בונה **רשימת החלטות עריכה** (EDL):
סדרת "ביטים", לכל אחד זמני מקור, מהירות, וזום. הרינדור מבצע את הרשימה
בגרף פילטרים אחד, כך שהתוצאה מרגישה ערוכה ולא חתוכה.

מה עורך טוב עושה, ומה המנוע מממש:

  אוויר מת        עורך חותך שתיקות בין משפטים. הן הדבר שהכי מסגיר
                  "וידאו גולמי". המנוע מזהה אותן ומסיר.
  שתיקה דרמטית    אבל לא כל שתיקה היא אוויר מת. "רגע… תקשיבו" חייב
                  את השתיקה שלו. המנוע מגן על שתיקות שערוץ ה-pause
                  סימן כמשמעותיות, ורק מקצר אותן.
  ראש וזנב        קליפ מתחיל על המילה הראשונה ונגמר על האחרונה,
                  לא על שנייה וחצי של כלום.
  שינוי זווית     בכל חיתוך, שינוי קל בגודל הפריים קורא כמו מעבר
                  בין שתי מצלמות. זה מה שהופך jump cut למכוון.
  דחיפה פנימה     על ביט השיא, זום איטי שמעלה מתח.
  ניקוי קליקים    כל חיתוך אודיו בלי fade מייצר נקישה. מעבר של
                  20 מילישניות בכל תפר פותר את זה.

סגנונות:
  raw       חיתוך ישיר, בלי שום התערבות. שימושי להשוואה.
  clean     הסרת אוויר מת + ראש/זנב צמודים + ליטוש אודיו.
  dynamic   + שינויי זווית בחיתוכים + דחיפה על השיא + צבע.
  hype      + הסרה אגרסיבית + יותר זוויות + האצה על קטעים מתים.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from .audio import AudioFeatures
from .scoring import Timeline
from .transcribe import TranscriptResult

log = logging.getLogger("polixor.editing")

EDIT_STYLES = ("raw", "clean", "dynamic", "hype")

# תקרת ביטים – מעבר לזה גרף הפילטרים נעשה כבד והקליפ קופצני מדי
MAX_BEATS = 36

# תקרת זום. חיתוך אנכי מ-16:9 כבר מגדיל פי ~1.78, וזום נוסף מעליו
# מרכך את התמונה. 1.18 הוא הגבול שבו שינוי הזווית עדיין ניכר לעין
# אבל החדות נשמרת.
MAX_ZOOM = 1.18


@dataclass
class StyleProfile:
    """הפרמטרים של סגנון עריכה. כולם ניתנים לעקיפה מההגדרות."""

    name: str = "clean"
    label: str = "נקי"
    description: str = ""

    # --- הסרת אוויר מת ---
    remove_silence: bool = True
    min_gap: float = 0.40          # שקט קצר מזה לא נוגעים בו
    keep_pad: float = 0.09         # כמה להשאיר משני צידי החיתוך
    max_removed_ratio: float = 0.28  # לא להסיר יותר מזה מאורך הקליפ
    dramatic_keep: float = 0.55    # שתיקה דרמטית מקוצרת לאורך הזה

    # --- ראש וזנב ---
    tighten_head: bool = True
    tighten_tail: bool = True
    head_lead: float = 0.28        # כמה להשאיר לפני המילה הראשונה
    tail_tail: float = 0.45        # כמה להשאיר אחרי המילה האחרונה

    # --- זוויות וזום ---
    angle_changes: bool = False
    zoom_levels: tuple[float, ...] = (1.0,)
    peak_push: float = 0.0         # דחיפה הדרגתית על ביט השיא (0 = כבוי)
    min_beat_for_zoom: float = 0.9

    # --- האצה ---
    speed_dead_air: float = 1.0    # האצת קטעים ללא דיבור (1.0 = כבוי)

    # --- ליטוש ---
    micro_fade: float = 0.020      # fade אודיו בכל תפר, למניעת נקישות
    audio_polish: bool = True
    color_punch: float = 0.0       # 0 = ללא, 1 = מלא
    sharpen: bool = False

    # --- כתוביות ---
    caption_animation: str = "none"   # none | pop | punch


STYLES: dict[str, StyleProfile] = {
    "raw": StyleProfile(
        name="raw", label="גולמי",
        description="חיתוך ישיר מהשידור, בלי שום עריכה. להשוואה.",
        remove_silence=False, tighten_head=False, tighten_tail=False,
        micro_fade=0.0, audio_polish=False, caption_animation="none",
    ),
    "clean": StyleProfile(
        name="clean", label="נקי",
        description="מסיר אוויר מת, מהדק את ההתחלה והסוף, ומלטש אודיו. "
                    "נשאר נאמן למקור.",
        remove_silence=True, min_gap=0.45, max_removed_ratio=0.25,
        angle_changes=False, zoom_levels=(1.0,), peak_push=0.0,
        audio_polish=True, color_punch=0.0, caption_animation="pop",
    ),
    "dynamic": StyleProfile(
        name="dynamic", label="דינמי",
        description="בנוסף: שינוי זווית בכל חיתוך, דחיפה על רגע השיא, "
                    "וצבע מעט חזק יותר. זה מה שמרגיש 'ערוך'.",
        remove_silence=True, min_gap=0.38, keep_pad=0.08,
        max_removed_ratio=0.32,
        angle_changes=True, zoom_levels=(1.0, 1.085, 1.0, 1.13),
        peak_push=0.07, audio_polish=True, color_punch=0.55, sharpen=True,
        caption_animation="pop",
    ),
    "hype": StyleProfile(
        name="hype", label="אנרגטי",
        description="הכי הדוק: הסרה אגרסיבית של שקט, הרבה שינויי זווית, "
                    "האצה על קטעים מתים וכתוביות קופצות. לשורטים.",
        remove_silence=True, min_gap=0.28, keep_pad=0.06,
        max_removed_ratio=0.42, dramatic_keep=0.42,
        head_lead=0.18, tail_tail=0.30,
        angle_changes=True, zoom_levels=(1.0, 1.10, 1.045, 1.16, 1.0, 1.13),
        peak_push=0.10, min_beat_for_zoom=0.65,
        speed_dead_air=1.35,
        audio_polish=True, color_punch=0.85, sharpen=True,
        caption_animation="punch",
    ),
}


def get_style(name: str) -> StyleProfile:
    return STYLES.get(name or "clean", STYLES["clean"])


# --------------------------------------------------------------------------
# ביטים ותכנית עריכה
# --------------------------------------------------------------------------
@dataclass
class Beat:
    """
    קטע אחד בתוצר הסופי.

    `src_start` / `src_end` הם **יחסית לתחילת חלון הקליפ** (לא לשידור),
    כי FFmpeg מקבל את החלון כבר חתוך עם ‎-ss.
    """

    src_start: float
    src_end: float
    zoom: float = 1.0
    # יעד דחיפה. בבניית התכנית הביט מתפצל לתת-ביטים עם זום עולה,
    # כי פילטר crop של FFmpeg מעריך את המידות פעם אחת בלבד ולכן
    # זום רציף כפונקציה של הזמן אינו אפשרי דרכו.
    zoom_to: float = 0.0
    speed: float = 1.0
    reason: str = ""
    # תכנית המסגור לביט הזה (`ReframePlan` מוגבלת לטווח שלו, בזמני
    # החלון). ריק => מסגור הקליפ כולו.
    reframe: Optional[Any] = None

    @property
    def src_duration(self) -> float:
        return max(0.0, self.src_end - self.src_start)

    @property
    def out_duration(self) -> float:
        return self.src_duration / max(0.05, self.speed)


@dataclass
class EditPlan:
    """תכנית העריכה המלאה של קליפ אחד."""

    beats: list[Beat] = field(default_factory=list)
    style: str = "clean"
    window_start: float = 0.0       # תחילת החלון בשידור המקורי
    window_end: float = 0.0
    raw_duration: float = 0.0       # אורך לפני עריכה
    removed_seconds: float = 0.0
    dramatic_kept: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def is_trivial(self) -> bool:
        """תכנית שהיא בעצם חיתוך רגיל – אפשר לרנדר במסלול הפשוט."""
        return (len(self.beats) <= 1
                and all(abs(b.zoom - 1.0) < 1e-3 and b.zoom_to <= 0
                        and abs(b.speed - 1.0) < 1e-3 for b in self.beats))

    @property
    def has_bound_reframe(self) -> bool:
        return any(b.reframe is not None for b in self.beats)

    @property
    def out_duration(self) -> float:
        return sum(b.out_duration for b in self.beats)

    @property
    def cut_count(self) -> int:
        return max(0, len(self.beats) - 1)

    def map_time(self, window_time: float) -> Optional[float]:
        """
        ממפה זמן בחלון המקור לזמן בתוצר הערוך.
        מחזיר None אם הרגע הזה נחתך החוצה.
        """
        out = 0.0
        for b in self.beats:
            if window_time < b.src_start:
                return out          # נפל בתוך חיתוך – מצמידים לתפר
            if window_time <= b.src_end:
                return out + (window_time - b.src_start) / max(0.05, b.speed)
            out += b.out_duration
        return None

    def map_span(self, start: float, end: float) -> Optional[tuple[float, float]]:
        """
        ממפה טווח (למשל כתובית). מחזיר None אם הטווח נחתך כולו.
        טווח שנחתך חלקית מתכווץ לחלק ששרד.
        """
        s = self.map_time(start)
        e = self.map_time(end)
        if s is None and e is None:
            return None
        if s is None:
            s = 0.0
        if e is None:
            e = self.out_duration
        if e <= s + 0.04:
            return None
        return (round(s, 3), round(e, 3))

    def stats(self) -> dict[str, Any]:
        return {
            "style": self.style,
            "beats": len(self.beats),
            "cuts": self.cut_count,
            "raw_duration": round(self.raw_duration, 2),
            "out_duration": round(self.out_duration, 2),
            "removed_seconds": round(self.removed_seconds, 2),
            "removed_percent": round(
                100.0 * self.removed_seconds / max(0.01, self.raw_duration), 1),
            "dramatic_pauses_kept": self.dramatic_kept,
            "zoom_changes": sum(1 for b in self.beats if abs(b.zoom - 1.0) > 1e-3),
            "notes": self.notes,
        }


MAX_BEATS = 160      # תקרת ביטים לחלק אחד (כל ביט = trim בגרף הפילטרים)


def cap_beats(beats: list[Beat], max_beats: int = MAX_BEATS) -> list[Beat]:
    """
    מגביל את מספר הביטים (באג 15): עריכה של 4 שעות יכולה לייצר אלפי
    חיתוכים קטנים, וגרף פילטרים כזה איטי ושביר. מחברים מחדש את החיתוכים
    הקצרים ביותר קודם – מחזירים שתיקה קצרה במקום לאבד תוכן – עד שעומדים
    בתקרה. ביטים עם זום או מהירות שונים לא מתאחדים לביט אחד בלי צורך:
    המאוחד מקבל את ערכי הביט הארוך מבין השניים.
    """
    if len(beats) <= max_beats:
        return beats
    # בדיוק len - max החיתוכים הקצרים ביותר מתחברים (גם כשיש ערכים זהים)
    order = sorted(range(len(beats) - 1),
                   key=lambda i: (beats[i + 1].src_start - beats[i].src_end, i))
    join = set(order[:len(beats) - max_beats])
    out: list[Beat] = [beats[0]]
    for i, b in enumerate(beats[1:]):
        prev = out[-1]
        if i in join:
            longer = prev if prev.src_duration >= b.src_duration else b
            out[-1] = Beat(src_start=prev.src_start, src_end=b.src_end, zoom=longer.zoom,
                           zoom_to=0.0, speed=longer.speed, reason=longer.reason,
                           reframe=prev.reframe if prev.reframe is b.reframe else None)
        else:
            out.append(b)
    return out


def output_to_source(beats: list[dict[str, Any]],
                     segments: list[tuple[float, float]]) -> Callable[[float], float]:
    """
    ממפה זמן בתוצר ערוך שכבר רונדר לזמן בשידור המקורי.

    `beats` הם הביטים כפי שנשמרו ב-`render_params["beats"]` (זמנים יחסיים
    לחלון של כל חלק), ו-`segments` הם חלונות החלקים בשידור. ביט שמתחיל
    לפני סוף הביט הקודם פותח חלק חדש. בלי ביטים (קליפים ישנים) – החלקים
    רצופים במהירות רגילה.

    משמש בייצוא חוזר: הכתוביות השמורות הן בזמני התוצר הקודם, ותכנית
    העריכה החדשה מקבלת זמני מקור.
    """
    segs = [(float(a), float(b)) for a, b in (segments or [])] or [(0.0, 0.0)]
    pieces: list[tuple[float, float, float, float]] = []   # out0, out1, src0, speed
    out = 0.0
    part = 0
    prev_end: Optional[float] = None
    for b in beats or []:
        try:
            s0, s1 = float(b["start"]), float(b["end"])
            speed = max(0.05, float(b.get("speed") or 1.0))
        except (KeyError, TypeError, ValueError):
            continue
        if prev_end is not None and s0 < prev_end - 0.01:
            part += 1
        prev_end = s1
        base = segs[min(part, len(segs) - 1)][0]
        dur = max(0.0, s1 - s0) / speed
        pieces.append((out, out + dur, base + s0, speed))
        out += dur
    if not pieces:
        for a, b in segs:
            pieces.append((out, out + max(0.0, b - a), a, 1.0))
            out += max(0.0, b - a)

    def mapper(t: float) -> float:
        for o0, o1, src, speed in pieces:
            if t < o1:
                return src + max(0.0, t - o0) * speed
        o0, o1, src, speed = pieces[-1]
        return src + (o1 - o0) * speed + max(0.0, t - o1)

    return mapper


# --------------------------------------------------------------------------
# בניית התכנית
# --------------------------------------------------------------------------
def build_edit_plan(
    *,
    clip_start: float,
    clip_end: float,
    peak_time: float,
    style: StyleProfile,
    audio: Optional[AudioFeatures],
    timeline: Optional[Timeline],
    transcript: Optional[TranscriptResult],
    silences: Optional[list[tuple[float, float]]] = None,
) -> EditPlan:
    """
    בונה את תכנית העריכה לקליפ. כל הזמנים בפלט יחסיים לתחילת החלון.
    """
    window = max(0.05, clip_end - clip_start)
    plan = EditPlan(style=style.name, window_start=clip_start,
                    window_end=clip_end, raw_duration=window)

    if style.name == "raw":
        plan.beats = [Beat(0.0, window, reason="חיתוך ישיר")]
        plan.notes.append("סגנון גולמי: לא בוצעה עריכה.")
        return plan

    # ---- 1. הידוק ראש וזנב על גבולות דיבור ----
    head, tail = 0.0, window
    if transcript is not None and transcript.has_speech:
        words = transcript.words_between(clip_start, clip_end)
        if words:
            if style.tighten_head:
                first = max(clip_start, words[0].start - style.head_lead)
                head = max(0.0, first - clip_start)
            if style.tighten_tail:
                last = min(clip_end, words[-1].end + style.tail_tail)
                tail = min(window, last - clip_start)
    if tail - head < 2.0:           # הידוק אגרסיבי מדי – מוותרים עליו
        head, tail = 0.0, window
    elif head > 0.05 or tail < window - 0.05:
        plan.notes.append(
            f"הידוק ראש/זנב: הוסרו {head:.1f} שנ' בהתחלה "
            f"ו-{window - tail:.1f} שנ' בסוף.")

    # ---- 2. איתור אוויר מת ----
    gaps: list[tuple[float, float, bool]] = []   # (התחלה, סוף, דרמטי?)
    if style.remove_silence:
        gaps = _find_dead_air(
            head, tail, clip_start=clip_start, style=style,
            audio=audio, timeline=timeline, transcript=transcript,
            silences=silences,
        )

    # ---- 3. הפיכת הפערים לביטים ----
    beats: list[Beat] = []
    cursor = head
    removed = 0.0
    dramatic = 0
    budget = style.max_removed_ratio * (tail - head)

    for g0, g1, is_dramatic in gaps:
        if g0 <= cursor:
            continue
        gap_len = g1 - g0

        if is_dramatic:
            # שתיקה משמעותית – מקצרים, לא מוחקים
            keep = min(gap_len, style.dramatic_keep)
            drop = gap_len - keep
            if drop < 0.12:
                continue
            if removed + drop > budget:
                continue
            beats.append(Beat(cursor, g0 + keep, reason="קטע + שתיקה דרמטית"))
            cursor = g1
            removed += drop
            dramatic += 1
            continue

        if removed + gap_len > budget:
            continue
        beats.append(Beat(cursor, g0, reason="קטע דיבור"))
        cursor = g1
        removed += gap_len

    if cursor < tail - 0.05:
        beats.append(Beat(cursor, tail, reason="קטע אחרון"))

    if not beats:
        beats = [Beat(head, tail, reason="ללא חיתוכים")]

    beats = _merge_tiny(beats, min_len=0.22)
    if len(beats) > MAX_BEATS:
        beats = _reduce_beats(beats, MAX_BEATS)
        plan.notes.append(f"מספר החיתוכים הוגבל ל-{MAX_BEATS} לשמירה על קצב נעים.")

    plan.removed_seconds = removed
    plan.dramatic_kept = dramatic
    if removed > 0.2:
        plan.notes.append(
            f"הוסרו {removed:.1f} שניות של אוויר מת ב-{len(beats) - 1} חיתוכים"
            + (f", תוך שמירה על {dramatic} שתיקות דרמטיות." if dramatic else "."))

    # ---- 4. זוויות וזום ----
    if style.angle_changes and len(style.zoom_levels) > 1:
        _assign_angles(beats, style, peak_time - clip_start)

    # ---- 4ב. פיצול דחיפות לתת-ביטים ----
    beats = _expand_pushes(beats)

    # ---- 5. האצה על קטעים ללא דיבור ----
    if style.speed_dead_air > 1.01 and transcript is not None:
        _assign_speed(beats, style, clip_start, transcript)

    plan.beats = beats
    return plan


# --------------------------------------------------------------------------
def _find_dead_air(
    head: float, tail: float, *, clip_start: float, style: StyleProfile,
    audio: Optional[AudioFeatures], timeline: Optional[Timeline],
    transcript: Optional[TranscriptResult],
    silences: Optional[list[tuple[float, float]]],
) -> list[tuple[float, float, bool]]:
    """
    מאתר פערים שראוי לחתוך. מחזיר (התחלה, סוף, האם דרמטי) בזמני חלון.

    מקור ראשון: רווחים בין מילים בתמלול – הכי מדויק.
    מקור שני: מסכת השקט מניתוח האודיו – עובד גם בלי תמלול.
    """
    gaps: list[tuple[float, float, bool]] = []

    if transcript is not None and transcript.has_speech:
        words = transcript.words_between(clip_start + head, clip_start + tail)
        prev_end: Optional[float] = None
        for w in words:
            if prev_end is not None:
                gap = w.start - prev_end
                if gap >= style.min_gap:
                    g0 = prev_end - clip_start + style.keep_pad
                    g1 = w.start - clip_start - style.keep_pad
                    if g1 - g0 >= 0.1:
                        gaps.append((g0, g1, False))
            prev_end = max(prev_end or 0.0, w.end)

    elif audio is not None and audio.n:
        for s0, s1 in _silence_runs(audio, clip_start + head, clip_start + tail,
                                    style.min_gap):
            g0 = s0 - clip_start + style.keep_pad
            g1 = s1 - clip_start - style.keep_pad
            if g1 - g0 >= 0.1:
                gaps.append((g0, g1, False))

    elif silences:
        for s0, s1 in silences:
            if s1 <= clip_start + head or s0 >= clip_start + tail:
                continue
            if (s1 - s0) < style.min_gap:
                continue
            g0 = max(head, s0 - clip_start + style.keep_pad)
            g1 = min(tail, s1 - clip_start - style.keep_pad)
            if g1 - g0 >= 0.1:
                gaps.append((g0, g1, False))

    # סימון שתיקות דרמטיות: אלה שערוץ ה-pause סימן כמשמעותיות
    if timeline is not None and timeline.n:
        marked: list[tuple[float, float, bool]] = []
        for g0, g1, _ in gaps:
            after = timeline.window_mean(
                timeline.pause, clip_start + g1, clip_start + g1 + 2.5)
            is_dramatic = after > 0.30 and (g1 - g0) >= 0.7
            marked.append((g0, g1, is_dramatic))
        gaps = marked

    gaps.sort(key=lambda g: g[0])
    return gaps


def _silence_runs(audio: AudioFeatures, start: float, end: float,
                  min_len: float) -> list[tuple[float, float]]:
    """רצפי שקט מתוך מסכת השקט של ניתוח האודיו."""
    if audio.n == 0:
        return []
    i0, i1 = audio.index_at(start), min(audio.n, audio.index_at(end) + 1)
    mask = np.asarray(audio.silence[i0:i1], dtype=bool)
    out: list[tuple[float, float]] = []
    i = 0
    while i < mask.size:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < mask.size and mask[j]:
            j += 1
        length = (j - i) * audio.hop
        if length >= min_len:
            out.append((start + i * audio.hop, start + j * audio.hop))
        i = max(j, i + 1)
    return out


def _merge_tiny(beats: list[Beat], *, min_len: float) -> list[Beat]:
    """ביט קצר מדי נבלע בקודם – מונע רצף קופצני של חיתוכים."""
    out: list[Beat] = []
    for b in beats:
        if b.src_duration < min_len and out:
            out[-1].src_end = b.src_end
        else:
            out.append(b)
    return [b for b in out if b.src_duration > 0.05]


def _reduce_beats(beats: list[Beat], target: int) -> list[Beat]:
    """מיזוג הביטים הקצרים ביותר עד שמגיעים למספר היעד."""
    work = list(beats)
    while len(work) > target:
        idx = min(range(len(work)), key=lambda i: work[i].src_duration)
        if idx == 0:
            work[1].src_start = work[0].src_start
            work.pop(0)
        else:
            work[idx - 1].src_end = work[idx].src_end
            work.pop(idx)
    return work


def _assign_angles(beats: list[Beat], style: StyleProfile,
                   peak_in_window: float) -> None:
    """
    נותן לכל ביט גודל פריים. השינוי קורה בדיוק בחיתוך, ולכן הוא נקרא
    כמעבר בין מצלמות ולא כזום אמצע-שוט.
    """
    levels = list(style.zoom_levels)
    k = 0
    peak_idx = -1
    cursor = 0.0
    for i, b in enumerate(beats):
        if b.src_start <= peak_in_window <= b.src_end:
            peak_idx = i
        cursor += b.out_duration

    for i, b in enumerate(beats):
        if b.src_duration < style.min_beat_for_zoom:
            b.zoom = 1.0
            continue
        b.zoom = min(MAX_ZOOM, levels[k % len(levels)])
        k += 1

    # ביט השיא: דחיפה הדרגתית פנימה
    if peak_idx >= 0 and style.peak_push > 0.001:
        b = beats[peak_idx]
        if b.src_duration >= 1.1:
            b.zoom = max(1.0, min(MAX_ZOOM, b.zoom))
            b.zoom_to = round(min(MAX_ZOOM, b.zoom + style.peak_push), 4)
            b.reason += " · דחיפה על השיא"


def _expand_pushes(beats: list[Beat]) -> list[Beat]:
    """
    הופך דחיפה מתמשכת לדחיפה מדורגת: הביט מתפצל לשניים-שלושה
    חלקים עם זום עולה. זה מהלך עריכה מוכר ("punch-in"), והוא גם
    מה שאפשרי טכנית – crop של FFmpeg קובע מידות פעם אחת.
    """
    out: list[Beat] = []
    for b in beats:
        if b.zoom_to <= 0.001 or abs(b.zoom_to - b.zoom) < 0.004:
            b.zoom_to = 0.0
            out.append(b)
            continue

        steps = 3 if b.src_duration >= 1.8 else (2 if b.src_duration >= 1.0 else 1)
        if steps < 2:
            b.zoom = round((b.zoom + b.zoom_to) / 2.0, 4)
            b.zoom_to = 0.0
            out.append(b)
            continue

        span = b.src_duration / steps
        for i in range(steps):
            z = min(MAX_ZOOM, b.zoom + (b.zoom_to - b.zoom) * i / (steps - 1))
            out.append(Beat(
                src_start=round(b.src_start + i * span, 4),
                src_end=round(b.src_start + (i + 1) * span, 4),
                zoom=round(z, 4), zoom_to=0.0, speed=b.speed,
                reason=f"{b.reason} · דחיפה {i + 1}/{steps}",
            ))
    return out


def _assign_speed(beats: list[Beat], style: StyleProfile, clip_start: float,
                  transcript: TranscriptResult) -> None:
    """מאיץ ביטים שאין בהם דיבור כלל, במקום לחתוך אותם לגמרי."""
    for b in beats:
        if b.src_duration < 0.8:
            continue
        words = transcript.words_between(clip_start + b.src_start,
                                         clip_start + b.src_end)
        speech = sum(w.end - w.start for w in words)
        if speech / b.src_duration < 0.12:
            b.speed = style.speed_dead_air
            b.reason += " · מואץ (ללא דיבור)"


# --------------------------------------------------------------------------
# קשירת מסגור לביטים
# --------------------------------------------------------------------------
def bind_reframe(plan: EditPlan, reframe: Any) -> EditPlan:
    """
    קושר תכנית מסגור (ReframePlan) לביטים של תכנית העריכה.

    כשהמסגור מתחלף באמצע הקליפ (למשל תגובה → מצלמה), ביט שחוצה את
    נקודת המעבר מתפצל בה, וכל חלק מקבל את המסגור שלו. זמני הביטים
    ושל המסגור הם באותו ציר – זמני החלון של הקליפ.
    """
    if reframe is None:
        return plan
    cuts = sorted(t for t in getattr(reframe, "piece_bounds", lambda: [])()
                  if t > 1e-3)
    beats: list[Beat] = []
    for b in plan.beats:
        pieces = [b]
        for cut in cuts:
            last = pieces[-1]
            if last.src_start + 0.05 < cut < last.src_end - 0.05:
                first = Beat(last.src_start, cut, zoom=last.zoom, zoom_to=0.0,
                             speed=last.speed, reason=last.reason)
                second = Beat(cut, last.src_end, zoom=last.zoom, zoom_to=0.0,
                              speed=last.speed, reason=last.reason)
                pieces[-1:] = [first, second]
        for piece in pieces:
            sub = reframe.restrict(piece.src_start, piece.src_end)
            if getattr(sub, "has_composite", False):
                # זום על פריים מורכב (שני פאנלים) היה חותך פנים או תוכן
                piece.zoom, piece.zoom_to = 1.0, 0.0
            piece.reframe = sub
            beats.append(piece)
    out = EditPlan(beats=beats, style=plan.style, window_start=plan.window_start,
                   window_end=plan.window_end, raw_duration=plan.raw_duration,
                   removed_seconds=plan.removed_seconds,
                   dramatic_kept=plan.dramatic_kept, notes=list(plan.notes))
    return out


# --------------------------------------------------------------------------
# הפיכת התכנית לגרף פילטרים
# --------------------------------------------------------------------------
def plan_to_filtergraph(
    plan: EditPlan,
    *,
    geometry_filter: str,
    out_width: int,
    out_height: int,
    style: StyleProfile,
    has_audio: bool,
    fade_seconds: float = 0.0,
    reframe: Any = None,
    src_width: int = 0,
    src_height: int = 0,
    geometry_builder: Optional[Callable[[int, Beat], str]] = None,
) -> tuple[str, str, str]:
    """
    בונה filter_complex מלא לתכנית.

    `geometry_filter` הוא שרשרת ההמרה לפריים היעד (Reframe או pad).
    היא מוחלת **לפני** setpts, כך שביטוי מעקב הפנים עדיין רואה את
    זמן המקור הנכון.

    כשלביט יש מסגור משלו (`bind_reframe`), או כשמועבר `reframe`,
    הגיאומטריה נבנית לכל ביט בנפרד עם תוויות ייחודיות – תוויות כפולות
    בין ביטים שוברות את הגרף (split / blur_pad / תגובה).

    מחזיר (filter_complex, תווית_וידאו, תווית_אודיו).
    """
    parts: list[str] = []
    v_labels: list[str] = []
    a_labels: list[str] = []
    fade = max(0.0, style.micro_fade)
    if reframe is not None and not plan.has_bound_reframe:
        plan = bind_reframe(plan, reframe)

    for i, b in enumerate(plan.beats):
        v_in = f"[0:v]"
        if geometry_builder is not None:
            geometry = geometry_builder(i, b)
        elif b.reframe is not None and src_width and src_height:
            from .reframe import build_vertical_filter

            geometry = build_vertical_filter(b.reframe, out_width=out_width,
                                             out_height=out_height,
                                             src_width=src_width,
                                             src_height=src_height, label=f"g{i}")
        else:
            geometry = _relabel(geometry_filter, f"g{i}")
        chain = [
            f"trim=start={b.src_start:.4f}:end={b.src_end:.4f}",
            geometry,
        ]

        zoom_chain = _zoom_filter(b, out_width, out_height)
        if zoom_chain:
            chain.append(zoom_chain)

        chain.append("setpts=PTS-STARTPTS")
        if abs(b.speed - 1.0) > 1e-3:
            chain.append(f"setpts=PTS/{b.speed:.4f}")

        parts.append(f"{v_in}{','.join(c for c in chain if c)}[v{i}]")
        v_labels.append(f"[v{i}]")

        if has_audio:
            a_chain = [
                f"atrim=start={b.src_start:.4f}:end={b.src_end:.4f}",
                "asetpts=PTS-STARTPTS",
            ]
            if abs(b.speed - 1.0) > 1e-3:
                a_chain.append(_atempo_chain(b.speed))
            if fade > 0.001 and b.out_duration > fade * 2.5:
                a_chain.append(f"afade=t=in:st=0:d={fade:.3f}")
                a_chain.append(
                    f"afade=t=out:st={max(0.0, b.out_duration - fade):.3f}:d={fade:.3f}")
            parts.append(f"[0:a]{','.join(a_chain)}[a{i}]")
            a_labels.append(f"[a{i}]")

    n = len(plan.beats)
    if has_audio:
        interleaved = "".join(v + a for v, a in zip(v_labels, a_labels))
        parts.append(f"{interleaved}concat=n={n}:v=1:a=1[vcat][acat]")
        v_out, a_out = "[vcat]", "[acat]"
    else:
        parts.append(f"{''.join(v_labels)}concat=n={n}:v=1:a=0[vcat]")
        v_out, a_out = "[vcat]", ""

    # ---- ליטוש על התוצר המחובר ----
    post: list[str] = []
    if style.color_punch > 0.01:
        p = min(1.0, style.color_punch)
        post.append(
            f"eq=contrast={1.0 + 0.09 * p:.3f}:saturation={1.0 + 0.16 * p:.3f}"
            f":brightness={0.012 * p:.4f}")
    if style.sharpen:
        post.append("unsharp=5:5:0.45:5:5:0.0")

    if post:
        parts.append(f"{v_out}{','.join(post)}[vpost]")
        v_out = "[vpost]"

    return ";".join(parts), v_out, a_out


def _relabel(chain: str, prefix: str) -> str:
    """
    מוסיף קידומת לתוויות פנימיות בשרשרת (`[bp_bg]` → `[g3bp_bg]`), כדי
    שאותה שרשרת תוכל להופיע בכמה ביטים באותו גרף.
    """
    import re

    if "[" not in chain:
        return chain
    return re.sub(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", lambda m: f"[{prefix}{m.group(1)}]", chain)


def _zoom_filter(beat: Beat, out_w: int, out_h: int) -> str:
    """
    זום קבוע על הפריים שכבר הומר לגודל היעד: חותכים פנימה ומגדילים
    בחזרה. כשהזום משתנה בדיוק בנקודת חיתוך, העין קוראת את זה כמעבר
    בין שתי מצלמות.

    זום משתנה בתוך ביט אינו אפשרי כאן: פילטר crop מעריך את w/h פעם
    אחת בהגדרת הגרף, בלי גישה ל-t. דחיפות מפוצלות מראש לתת-ביטים
    ב-`_expand_pushes`, ולכן כאן תמיד מגיע זום קבוע.
    """
    zoom = beat.zoom
    if beat.zoom_to > 0.001:        # רשת ביטחון, לא אמור לקרות
        zoom = (beat.zoom + beat.zoom_to) / 2.0

    if abs(zoom - 1.0) < 0.002:
        return ""

    cw = max(2, int(out_w / zoom) // 2 * 2)
    ch = max(2, int(out_h / zoom) // 2 * 2)
    return (f"crop={cw}:{ch}:(in_w-{cw})/2:(in_h-{ch})/2,"
            f"scale={out_w}:{out_h}:flags=bicubic,setsar=1")


def _atempo_chain(speed: float) -> str:
    """atempo תומך ב-0.5..2.0; מעבר לזה משרשרים."""
    speed = max(0.25, min(4.0, speed))
    parts: list[str] = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.4f}")
    return ",".join(parts)


def audio_polish_chain(style: StyleProfile, normalize: bool) -> str:
    """
    ליטוש אודיו: ניקוי רעש נמוך, איזון עוצמות, דחיסה קלה ולימיטר.
    הלימיטר בסוף מבטיח שהפלט לא ייחתך גם אחרי הדחיסה.
    """
    chain: list[str] = []
    if style.audio_polish:
        chain.append("highpass=f=65")
    if normalize:
        chain.append("dynaudnorm=f=220:g=13:p=0.93:m=11:s=9")
    if style.audio_polish:
        chain.append("acompressor=threshold=-19dB:ratio=3:attack=12:release=180:makeup=2")
        chain.append("alimiter=limit=0.95:level=disabled")
    chain.append("aresample=async=1:first_pts=0")
    return ",".join(chain)


def describe_plan(plan: EditPlan, style: StyleProfile) -> str:
    """תיאור קריא של מה שהעורך עשה – מוצג למשתמש במסך העריכה."""
    s = plan.stats()
    if plan.style == "raw":
        return "חיתוך ישיר מהשידור, ללא עריכה."

    bits: list[str] = [f"סגנון {style.label}"]
    if s["cuts"]:
        bits.append(f"{s['cuts']} חיתוכים")
    if s["removed_seconds"] > 0.2:
        bits.append(f"הוסרו {s['removed_seconds']:.1f} שנ' אוויר מת "
                    f"({s['removed_percent']:.0f}%)")
    if s["dramatic_pauses_kept"]:
        bits.append(f"{s['dramatic_pauses_kept']} שתיקות דרמטיות נשמרו")
    if s["zoom_changes"]:
        bits.append(f"{s['zoom_changes']} שינויי זווית")
    speeds = sum(1 for b in plan.beats if abs(b.speed - 1.0) > 1e-3)
    if speeds:
        bits.append(f"{speeds} קטעים מואצים")
    return " · ".join(bits)


# --------------------------------------------------------------------------
# חיבור להגדרות המשתמש
# --------------------------------------------------------------------------
def style_from_settings(settings: Any, *, vertical: bool) -> StyleProfile:
    """
    בוחר את סגנון העריכה לפי סוג הקליפ, ומחיל עליו את העקיפות
    הגלובליות מההגדרות. עקיפה בערך 0 פירושה "השאר כמו בסגנון".
    """
    import copy

    name = getattr(settings, "edit_style_short" if vertical else "edit_style_long",
                   "clean")
    style = copy.replace(get_style(name)) if hasattr(copy, "replace") \
        else StyleProfile(**{**get_style(name).__dict__})

    if not getattr(settings, "remove_silence", True):
        style.remove_silence = False
    if not getattr(settings, "angle_changes", True):
        style.angle_changes = False
        style.zoom_levels = (1.0,)
        style.peak_push = 0.0

    gap = float(getattr(settings, "silence_min_gap", 0.0) or 0.0)
    if gap > 0.01:
        style.min_gap = gap
    ratio = float(getattr(settings, "max_removed_ratio", 0.0) or 0.0)
    if ratio > 0.01:
        style.max_removed_ratio = ratio

    anim = getattr(settings, "subtitle_animation", None)
    if anim in ("none", "pop", "punch"):
        style.caption_animation = anim

    return style


def style_catalog() -> list[dict[str, Any]]:
    """רשימת הסגנונות לתצוגה בממשק."""
    return [
        {
            "name": s.name,
            "label": s.label,
            "description": s.description,
            "removes_silence": s.remove_silence,
            "angle_changes": s.angle_changes,
            "caption_animation": s.caption_animation,
            "color_punch": s.color_punch,
        }
        for s in (STYLES[k] for k in EDIT_STYLES)
    ]
