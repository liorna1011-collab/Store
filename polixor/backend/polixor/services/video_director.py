"""
AI Video Director — מבין את הסרטון, ואז מחליט.

זו השכבה המרכזית. היא מקבלת את כל מה שידוע על הסרטון — תמלול, תזמון
מילים, שתיקות, אותות אודיו, מסגור, נכסים זמינים וסגנון — ומחזירה
`VideoEditPlan`: תכנית עריכה מלאה ומוסברת.

עיקרון מרכזי: **קודם תכנון, אחר כך ביצוע.** המנוע הזה לא נוגע ב-FFmpeg,
לא כותב קבצים ולא משנה את ה-DB. הוא מחזיר החלטות. מי שמבצע אותן הוא
שלב הרינדור, וכך אפשר להציג את התכנית למשתמש, לתת לו לשנות אותה,
ולבצע רק אחרי אישור.

לכל החלטה יש `start`, `end`, `action`, `reason`, `confidence` ו-`priority`.
ה-`reason` נכתב בעברית ומיועד להצגה למשתמש במסך „למה ה-AI עשה את זה".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

from ..config import AppSettings
from .audio import AudioFeatures
from .hook_engine import HookAnalysis, analyze_hook
from .pacing_engine import (
    DEFAULT_PROFILE, PacingPlan, PacingProfile, get_profile, plan_pacing,
)
from .semantics import (
    DROPPABLE_ROLES, EMPHASIS_ROLES, ROLE_LABELS_HE, Disfluency,
    SemanticAnalysis, Sentence,
)
from .timeline import Segment, TimelineMap
from .transcribe import TranscriptResult

log = logging.getLogger("polixor.director")


# --------------------------------------------------------------------------
# החלטות
# --------------------------------------------------------------------------
class Action(str, Enum):
    CUT = "cut"                          # להסיר קטע
    TRIM_HEAD = "trim_head"              # לגזום מהתחלה
    KEEP_PAUSE = "keep_pause"            # לשמור שתיקה בכוונה
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    HOLD_FRAME = "hold_frame"            # לא לגעת במסגור כאן
    EMPHASIZE_WORD = "emphasize_word"
    SUGGEST_BROLL = "suggest_broll"
    MOVE_HOOK = "move_hook"              # דורש אישור משתמש


class Priority(int, Enum):
    MUST = 1        # בלי זה התוצאה פגומה
    SHOULD = 2      # משפר משמעותית
    NICE = 3        # תוספת, אפשר לוותר


@dataclass
class Decision:
    """החלטת עריכה אחת, עם כל מה שצריך כדי להסביר אותה ולבטל אותה."""

    action: str
    start: float
    end: float
    reason: str
    confidence: float = 0.5
    priority: int = Priority.SHOULD.value
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "heuristic"       # heuristic | llm | user
    enabled: bool = True
    requires_approval: bool = False
    id: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "priority": self.priority,
            "params": self.params,
            "source": self.source,
            "enabled": self.enabled,
            "requires_approval": self.requires_approval,
        }


# --------------------------------------------------------------------------
# מסגור בטוח
# --------------------------------------------------------------------------
# כמה מתיחה כוללת מהמקור עדיין נראית סבירה במסך טלפון. מעבר לזה
# התמונה מתרככת בצורה מורגשת.
#
# חשוב להבין את המספר: חיתוך אנכי 9:16 ממקור 16:9 **כבר** מותח פי
# 1.78, וזה המחיר הקבוע של הפורמט. לכן הסף אינו „בלי מתיחה" — הוא
# תקרה כוללת, והזום מקבל רק את מה שנשאר מתחתיה.
MAX_TOTAL_UPSCALE = 2.0
# תקרה מוחלטת גם כשהמקור ענק: זום גדול מזה נראה מלאכותי בטוקינג-הד
ABSOLUTE_MAX_ZOOM = 1.30


def baseline_upscale(src_w: int, src_h: int, out_w: int, out_h: int) -> float:
    """כמה המקור נמתח כבר בגלל החיתוך ליחס היעד, לפני שום זום."""
    if src_w <= 0 or src_h <= 0 or out_w <= 0 or out_h <= 0:
        return 1.0
    crop_w = min(float(src_w), float(src_h) * (out_w / out_h))
    if crop_w <= 1.0:
        return 1.0
    return float(out_w) / crop_w


def safe_zoom_limit(src_w: int, src_h: int, out_w: int, out_h: int, *,
                    max_total_upscale: float = MAX_TOTAL_UPSCALE) -> float:
    """
    תקרת הזום שמותרת בלי לרכך את התמונה מעבר לסביר.

    זום מקטין את אזור החיתוך, ולכן מכפיל את המתיחה הקיימת. הפונקציה
    מחזירה את הזום הגדול ביותר שבו המתיחה **הכוללת** עדיין מתחת
    ל-`max_total_upscale`.

    דוגמאות:
      1920×1080 → 1080×1920 : מתיחת בסיס 1.78 → זום עד 1.12
      3840×2160 → 1080×1920 : מתיחת בסיס 0.89 → זום עד התקרה המוחלטת
      1280×720  → 1080×1920 : מתיחת בסיס 2.67 → אין זום נוסף כלל

    כשהמידות אינן ידועות אין בסיס לחשב תקרה, ולכן לא מזמינים זום כלל
    במקום לנחש.
    """
    if src_w <= 0 or src_h <= 0 or out_w <= 0 or out_h <= 0:
        return 1.0
    base = baseline_upscale(src_w, src_h, out_w, out_h)
    if base <= 0:
        return 1.0
    limit = max_total_upscale / base
    return float(max(1.0, min(ABSOLUTE_MAX_ZOOM, limit)))


# --------------------------------------------------------------------------
# התכנית
# --------------------------------------------------------------------------
@dataclass
class VideoEditPlan:
    """
    תכנית העריכה המלאה. מבנה נתונים בלבד — שום דבר כאן לא בוצע עדיין.

    השדות הריקים אינם „טרם נבנו": הם קטגוריות שה-P0 אינו מאכלס
    (מוזיקה, סאונד דיזיין, מעברים). הם קיימים כדי שהמבנה יהיה יציב,
    ומסומנים ב-`unimplemented` כדי שלא ייראו כמו החלטה שהתקבלה.
    """

    # מקור
    source_start: float = 0.0
    source_end: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = True

    # הבנה
    hook: Optional[HookAnalysis] = None
    beats: list[dict[str, Any]] = field(default_factory=list)
    pacing: Optional[PacingPlan] = None

    # החלטות
    cuts: list[Decision] = field(default_factory=list)
    zoom_events: list[Decision] = field(default_factory=list)
    captions: list[Decision] = field(default_factory=list)
    broll: list[Decision] = field(default_factory=list)
    pending: list[Decision] = field(default_factory=list)   # דורש אישור

    # קטגוריות שאינן מאוכלסות בשלב הזה
    generated_visuals: list[Decision] = field(default_factory=list)
    sound_events: list[Decision] = field(default_factory=list)
    music: dict[str, Any] = field(default_factory=dict)
    transitions: list[Decision] = field(default_factory=list)
    color: dict[str, Any] = field(default_factory=dict)
    ending: dict[str, Any] = field(default_factory=dict)
    export_settings: dict[str, Any] = field(default_factory=dict)

    style: str = DEFAULT_PROFILE
    notes: list[str] = field(default_factory=list)
    unimplemented: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    @property
    def all_decisions(self) -> list[Decision]:
        return [*self.cuts, *self.zoom_events, *self.captions, *self.broll,
                *self.pending]

    @property
    def source_duration(self) -> float:
        return max(0.0, self.source_end - self.source_start)

    @property
    def removed_seconds(self) -> float:
        return sum(d.duration for d in self.cuts if d.enabled
                   and d.action in (Action.CUT.value, Action.TRIM_HEAD.value))

    @property
    def estimated_duration(self) -> float:
        return max(0.0, self.source_duration - self.removed_seconds)

    def enabled_cuts(self) -> list[Decision]:
        return [d for d in self.cuts if d.enabled
                and d.action in (Action.CUT.value, Action.TRIM_HEAD.value)]

    # ------------------------------------------------------------------
    def to_segments(self) -> list[Segment]:
        """
        הופך את החיתוכים לקטעים ששורדים — הקלט של מיפוי הזמנים.

        מה שלא נחתך, נשאר. זו הסיבה שהתכנית מנוסחת כרשימת הסרות ולא
        כרשימת שמירות: החלטה שבוטלה פשוט מחזירה את החומר.
        """
        cuts = sorted(self.enabled_cuts(), key=lambda d: d.start)
        segments: list[Segment] = []
        cursor = self.source_start
        for c in cuts:
            start = max(cursor, c.start)
            if start > cursor + 0.04:
                segments.append(Segment(cursor, start, reason="נשמר"))
            cursor = max(cursor, c.end)
        if cursor < self.source_end - 0.04:
            segments.append(Segment(cursor, self.source_end, reason="נשמר"))
        return segments

    def timeline(self) -> TimelineMap:
        return TimelineMap(self.to_segments())

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "source": {
                "start": round(self.source_start, 3),
                "end": round(self.source_end, 3),
                "duration": round(self.source_duration, 3),
                "width": self.width, "height": self.height,
                "fps": round(self.fps, 3), "has_audio": self.has_audio,
            },
            "style": self.style,
            "hook": self.hook.to_dict() if self.hook else None,
            "beats": self.beats,
            "pacing": self.pacing.to_dict() if self.pacing else None,
            "cuts": [d.to_dict() for d in self.cuts],
            "zoom_events": [d.to_dict() for d in self.zoom_events],
            "captions": [d.to_dict() for d in self.captions],
            "broll": [d.to_dict() for d in self.broll],
            "pending": [d.to_dict() for d in self.pending],
            "generated_visuals": [d.to_dict() for d in self.generated_visuals],
            "sound_events": [d.to_dict() for d in self.sound_events],
            "music": self.music,
            "transitions": [d.to_dict() for d in self.transitions],
            "color": self.color,
            "ending": self.ending,
            "export_settings": self.export_settings,
            "summary": {
                "cuts": len(self.enabled_cuts()),
                "removed_seconds": round(self.removed_seconds, 2),
                "removed_percent": round(
                    100.0 * self.removed_seconds
                    / max(0.01, self.source_duration), 1),
                "zoom_events": len([d for d in self.zoom_events if d.enabled]),
                "emphasis": len(self.captions),
                "broll_suggestions": len(self.broll),
                "needs_approval": len(self.pending),
                "estimated_duration": round(self.estimated_duration, 2),
            },
            "notes": self.notes,
            "unimplemented": self.unimplemented,
        }


# --------------------------------------------------------------------------
# בניית התכנית
# --------------------------------------------------------------------------
_next_id = 0


def _mk_id(prefix: str) -> str:
    global _next_id
    _next_id += 1
    return f"{prefix}{_next_id:04d}"


def direct(
    *,
    semantics: SemanticAnalysis,
    audio: Optional[AudioFeatures] = None,
    transcript: Optional[TranscriptResult] = None,
    source_start: float = 0.0,
    source_end: float = 0.0,
    width: int = 0,
    height: int = 0,
    out_width: int = 0,
    out_height: int = 0,
    fps: float = 0.0,
    has_audio: bool = True,
    style: str = DEFAULT_PROFILE,
    settings: Optional[AppSettings] = None,
    language: Optional[str] = None,
) -> VideoEditPlan:
    """
    מייצר תכנית עריכה מלאה. אינו מבצע דבר.

    עובד גם בלי תמלול: אז מתבססים על אותות אודיו בלבד, והתכנית
    מציינת במפורש מה לא היה זמין.
    """
    profile = get_profile(style)
    plan = VideoEditPlan(
        source_start=source_start,
        source_end=source_end or (source_start + 1.0),
        width=width, height=height, fps=fps, has_audio=has_audio,
        style=profile.name,
    )

    # ---- מה לא קיים בשלב הזה, ואיננו מעמידים פנים שכן ----
    plan.unimplemented = [
        "music", "sound_events", "transitions", "color", "ending",
        "generated_visuals",
    ]
    plan.notes.append(
        "התכנית מכסה חיתוכים, מסגור, כתוביות והצעות ויזואליות. "
        "מוזיקה, סאונד דיזיין, מעברים ותיקון צבע אינם מתוכננים בשלב הזה.")

    if not semantics.has_transcript:
        plan.notes.append(
            "אין תמלול: אי אפשר לזהות מילות מילוי, תפקידים נרטיביים או "
            "מילים להדגשה. העריכה מתבססת על אותות אודיו בלבד.")

    # ---- 1. הבנה ----
    language = language or semantics.language or None
    plan.hook = (analyze_hook(semantics.sentences, language=language)
                 if semantics.sentences else None)
    _reconcile_hook(semantics, plan.hook)
    plan.beats = [b.to_dict() for b in semantics.beats]
    plan.pacing = plan_pacing(semantics.beats, profile=profile,
                              total_duration=plan.source_duration)
    plan.notes.extend(plan.pacing.notes)

    # ---- 2. חיתוכים ----
    _plan_cuts(plan, semantics, audio, profile)

    # ---- 3. מסגור ----
    _plan_zooms(plan, semantics, profile,
                out_width=out_width or width, out_height=out_height or height)

    # ---- 4. הדגשות בכתוביות ----
    _plan_emphasis(plan, semantics, profile)

    # ---- 5. הצעות ויזואליות ----
    _plan_broll(plan, semantics, profile)

    # ---- 6. הצעות שדורשות אישור ----
    if plan.hook and plan.hook.suggested_move:
        m = plan.hook.suggested_move
        plan.pending.append(Decision(
            id=_mk_id("mv"), action=Action.MOVE_HOOK.value,
            start=m.source_start, end=m.source_end,
            reason=m.reason, confidence=min(0.9, m.score),
            priority=Priority.NICE.value,
            params={"sentence_index": m.sentence_index, "text": m.text,
                    "gain": round(m.gain, 3)},
            requires_approval=True, enabled=False))

    # ---- 7. ניקוי: החלטות שנפלו על חומר שהוסר ----
    _drop_decisions_on_removed_material(plan)

    # ---- 8. הגדרות ייצוא ----
    plan.export_settings = {
        "width": out_width or width,
        "height": out_height or height,
        "fps": fps,
        "safe_zoom_limit": round(safe_zoom_limit(
            width, height, out_width or width, out_height or height), 3),
    }
    return plan


# --------------------------------------------------------------------------
# חיתוכים
# --------------------------------------------------------------------------
def _plan_cuts(plan: VideoEditPlan, sem: SemanticAnalysis,
               audio: Optional[AudioFeatures], profile: PacingProfile) -> None:
    """
    בונה את רשימת ההסרות, בסדר עדיפות, עד לתקרת ההסרה של הסגנון.

    הסדר חשוב: קודם גיזום הפתיחה, אחר כך היסוסים ומילות מילוי (הם
    הכי מפריעים ועולים הכי מעט), ורק אז אוויר מת. כך, כשמגיעים
    לתקרה, מה שנשאר בפנים הוא מה שהכי שווה להשאיר.
    """
    budget = profile.max_removed_ratio * plan.source_duration
    used = 0.0
    candidates: list[Decision] = []

    # --- גיזום פתיחה ---
    # מטופל ראשון ובנפרד, כי הוא **מכיל** מועמדים אחרים: מילות המילוי
    # שבתוך הפתיחה כבר נחתכות איתו. אם נשאיר אותו למיון הרגיל, מילת
    # מילוי עם ביטחון גבוה יותר תיבחר לפניו והגיזום כולו יידחה כחופף.
    trim_until = plan.source_start
    if plan.hook and plan.hook.has_trim:
        # `trim_start` הוא זמן מוחלט בתמלול, באותה מערכת צירים כמו
        # `source_start` — לא היסט ממנה. חיבור השניים היה מזיז את
        # הגיזום אל מעבר לסוף הקליפ ומוחק את כולו.
        trim_until = min(plan.source_end,
                         max(plan.source_start, plan.hook.trim_start))
        head = Decision(
            id=_mk_id("cut"), action=Action.TRIM_HEAD.value,
            start=plan.source_start, end=trim_until,
            reason=plan.hook.trim_reason or "פתיחה בלי תוכן",
            confidence=0.85, priority=Priority.MUST.value,
            params={"kind": "head_trim"})
        plan.cuts.append(head)
        used += head.duration

    def _inside_trim(a: float, b: float) -> bool:
        return b <= trim_until + 0.05

    # --- היסוסים ומילות מילוי ---
    for d in sem.disfluencies:
        if d.duration < 0.08 or _inside_trim(d.start, d.end):
            continue
        kind_label = {
            "filler_word": "מילת מילוי",
            "filler_phrase": "ביטוי מילוי",
            "false_start": "התחלה כושלת",
            "repeat": "חזרה על מה שכבר נאמר",
        }.get(d.kind, d.kind)
        candidates.append(Decision(
            id=_mk_id("cut"), action=Action.CUT.value,
            start=d.start, end=d.end,
            reason=f"{kind_label}: „{d.text[:34]}”",
            confidence=d.confidence,
            priority=(Priority.MUST.value if d.confidence >= 0.85
                      else Priority.SHOULD.value),
            params={"kind": d.kind}))

    # --- משפטי מילוי שלמים ---
    for s in sem.sentences:
        if s.role != "filler" or s.duration < 0.5:
            continue
        if _inside_trim(s.start, s.end):
            continue
        candidates.append(Decision(
            id=_mk_id("cut"), action=Action.CUT.value,
            start=s.start, end=s.end,
            reason=f"משפט ללא תוכן חדש: „{s.text[:34]}”",
            confidence=min(0.8, 0.4 + s.filler_ratio),
            priority=Priority.SHOULD.value,
            params={"kind": "filler_sentence",
                    "sentence_index": s.index}))

    # --- אוויר מת ---
    protected = _protected_pauses(sem)
    for start, end in _silence_runs(audio, plan.source_start, plan.source_end,
                                    profile.silence_tolerance):
        if _inside_trim(start, end):
            continue
        mid = (start + end) / 2.0
        if any(a <= mid <= b for a, b in protected):
            plan.cuts.append(Decision(
                id=_mk_id("keep"), action=Action.KEEP_PAUSE.value,
                start=start, end=end,
                reason="שתיקה שנושאת משמעות — נשמרת בכוונה",
                confidence=0.7, priority=Priority.MUST.value,
                params={"kind": "dramatic_pause"}))
            continue
        candidates.append(Decision(
            id=_mk_id("cut"), action=Action.CUT.value,
            start=start, end=end,
            reason=f"אוויר מת ({end - start:.1f} שניות ללא דיבור)",
            confidence=0.75, priority=Priority.SHOULD.value,
            params={"kind": "dead_air"}))

    # --- מיון לפי עדיפות ואז ביטחון, ומילוי עד התקרה ---
    candidates.sort(key=lambda d: (d.priority, -d.confidence, d.start))
    accepted: list[Decision] = list(plan.cuts)   # הגיזום כבר בפנים
    for d in candidates:
        if _overlaps(d, accepted):
            continue
        if used + d.duration > budget and d.priority != Priority.MUST.value:
            d.enabled = False
            d.reason += " · לא בוצע: נגמרה מכסת ההסרה של הסגנון"
            accepted.append(d)
            continue
        used += d.duration
        accepted.append(d)

    plan.cuts = sorted(accepted, key=lambda d: d.start)

    skipped = [d for d in accepted if not d.enabled]
    if skipped:
        plan.notes.append(
            f"{len(skipped)} הסרות אפשריות לא בוצעו כדי לא לעבור את תקרת "
            f"ההסרה של הסגנון ({profile.max_removed_ratio:.0%}).")


def _drop_decisions_on_removed_material(plan: VideoEditPlan) -> None:
    """
    מסיר החלטות שנפלו כולן בתוך קטע שנחתך.

    בלי זה מתוכנן זום בשנייה הראשונה של סרטון שהשנייה הראשונה שלו
    נגזמה — החלטה שלא תתבצע לעולם, אבל תופיע למשתמש במסך התכנית
    ותיראה כמו באג.
    """
    removed = [(d.start, d.end) for d in plan.enabled_cuts()]
    if not removed:
        return

    def inside(d: Decision) -> bool:
        mid = (d.start + d.end) / 2.0
        return any(a - 0.02 <= mid <= b + 0.02 for a, b in removed)

    for name in ("zoom_events", "captions", "broll"):
        items: list[Decision] = getattr(plan, name)
        kept = [d for d in items if not inside(d)]
        dropped = len(items) - len(kept)
        if dropped:
            setattr(plan, name, kept)
            log.debug("dropped %d %s decisions on removed material",
                      dropped, name)


def _reconcile_hook(sem: SemanticAnalysis,
                    hook: Optional[HookAnalysis]) -> None:
    """
    מיישב בין הסיווג הסמנטי לבין מסקנת ה-Hook Engine.

    הסיווג הסמנטי מסמן „וו פתיחה" לפי מיקום, ולכן משפט של גרירת רגליים
    בתחילת הסרטון יכול לקבל את התפקיד. ה-Hook Engine בודק אותו לעומק
    ומחליט שהוא נגזם. בלי היישוב הזה נמשיך להתייחס אליו כאל הוו —
    ואפילו נדגיש בו מילה, כמו „אוקיי".
    """
    if hook is None or not sem.sentences:
        return
    if hook.has_trim:
        for s in sem.sentences:
            if s.end <= hook.trim_start + 0.05 and s.role != "filler":
                s.role = "filler"
                s.confidence = 0.75
                s.reason = "פתיחה ללא תוכן — נגזמת לפני הוו האמיתי"

    # מסמנים מחדש את המשפט שה-Hook Engine זיהה כוו בפועל
    for s in sem.sentences:
        if abs(s.start - hook.hook_start) < 0.05 and s.role != "hook":
            s.role = "hook"
            s.confidence = max(s.confidence, hook.strength)
            s.reason = hook.reason
            break

    # ה-beats נבנו לפני התיקון ולכן צריכים להיבנות מחדש
    from .semantics import group_beats
    sem.beats = group_beats(sem.sentences)


def _overlaps(d: Decision, existing: Sequence[Decision]) -> bool:
    for e in existing:
        if d.start < e.end - 0.02 and e.start < d.end - 0.02:
            return True
    return False


def _protected_pauses(sem: SemanticAnalysis) -> list[tuple[float, float]]:
    """
    שתיקות שאסור לגעת בהן: לפני שיא רגשי, אחרי שאלה, ולפני פאנץ'.

    זו ההבחנה שהמשתמש ביקש — בין שקט מת לשקט מכוון. שתיקה שמגיעה
    בדיוק לפני המשפט החזק ביותר היא חלק מהאפקט, לא תקלה.
    """
    out: list[tuple[float, float]] = []
    for prev, nxt in zip(sem.sentences, sem.sentences[1:]):
        gap = nxt.start - prev.end
        if gap < 0.45:
            continue
        if nxt.role in ("emotional_peak", "payoff") or prev.is_question:
            out.append((prev.end, nxt.start))
    return out


def _silence_runs(audio: Optional[AudioFeatures], start: float, end: float,
                  tolerance: float) -> list[tuple[float, float]]:
    """רצפי שקט ארוכים מהסובלנות של הסגנון."""
    if audio is None or audio.n == 0 or audio.silence.size == 0:
        return []
    runs: list[tuple[float, float]] = []
    hop = audio.hop or 0.1
    i0, i1 = audio.index_at(start), audio.index_at(end)
    run_start: Optional[int] = None
    for i in range(i0, min(i1 + 1, audio.silence.size)):
        if audio.silence[i]:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            a, b = run_start * hop, i * hop
            if b - a > tolerance:
                runs.append((a, b))
            run_start = None
    if run_start is not None:
        a, b = run_start * hop, min(i1, audio.silence.size - 1) * hop
        if b - a > tolerance:
            runs.append((a, b))
    return runs


# --------------------------------------------------------------------------
# מסגור
# --------------------------------------------------------------------------
def _plan_zooms(plan: VideoEditPlan, sem: SemanticAnalysis,
                profile: PacingProfile, *, out_width: int,
                out_height: int) -> None:
    """
    קובע שינויי מסגור — כל אחד עם הצדקה.

    אין זום אקראי. כל שינוי נובע מתפקיד הקטע: משפט חשוב מקבל דחיפה
    פנימה, מעבר נושא מקבל יציאה החוצה, ורגע רגשי מקבל התקרבות איטית
    ומוחזקת.
    """
    limit = safe_zoom_limit(plan.width, plan.height, out_width, out_height)
    base = baseline_upscale(plan.width, plan.height, out_width, out_height)
    if limit <= 1.005:
        plan.notes.append(
            f"החיתוך ליחס היעד כבר מותח את המקור פי {base:.2f} "
            f"({plan.width}×{plan.height} → {out_width}×{out_height}). "
            "זום נוסף היה מרכך את התמונה, ולכן המסגור נשאר קבוע.")
        return

    if not plan.pacing or not plan.pacing.sections:
        return

    for section in plan.pacing.sections:
        if section.zoom_changes <= 0:
            plan.zoom_events.append(Decision(
                id=_mk_id("frm"), action=Action.HOLD_FRAME.value,
                start=section.start, end=section.end,
                reason=f"{ROLE_LABELS_HE.get(section.role, section.role)}: "
                       "המסגור נשאר קבוע כאן",
                confidence=0.6, priority=Priority.NICE.value,
                params={"role": section.role}))
            continue

        action, target, why = _zoom_for_role(section.role, limit)
        # פורסים את השינויים על פני הקטע
        n = section.zoom_changes
        step = section.duration / (n + 1)
        for k in range(n):
            at = section.start + step * (k + 1)
            # מתקרבים בהדרגה לתקרה, לא קופצים אליה
            frac = (k + 1) / n
            zoom = 1.0 + (target - 1.0) * frac
            plan.zoom_events.append(Decision(
                id=_mk_id("zm"), action=action,
                start=round(at, 3), end=round(min(section.end, at + step), 3),
                reason=why,
                confidence=0.65,
                priority=Priority.NICE.value,
                params={"zoom": round(min(zoom, limit), 4),
                        "role": section.role,
                        "limit": round(limit, 4)}))

    plan.notes.append(
        f"תקרת הזום: {limit:.2f}×. החיתוך ליחס היעד כבר מותח פי "
        f"{base:.2f}, והתקרה מוודאת שהמתיחה הכוללת לא עוברת את "
        f"{MAX_TOTAL_UPSCALE:.1f}×.")


def _zoom_for_role(role: str, limit: float) -> tuple[str, float, str]:
    """(פעולה, יעד זום, נימוק) לפי תפקיד הקטע."""
    if role in ("emotional_peak", "payoff"):
        return (Action.ZOOM_IN.value, min(limit, 1.12),
                "התקרבות איטית — הרגע הרגשי של הסרטון")
    if role in ("key_claim", "main_idea"):
        return (Action.ZOOM_IN.value, min(limit, 1.09),
                "דחיפה פנימה על המשפט שנושא את המסר")
    if role == "hook":
        return (Action.ZOOM_IN.value, min(limit, 1.07),
                "דחיפה קלה בפתיחה כדי לייצר תנועה מיד")
    if role == "topic_change":
        return (Action.ZOOM_OUT.value, 1.0,
                "יציאה החוצה — מסמנת מעבר לנושא חדש")
    if role == "cta":
        return (Action.ZOOM_IN.value, min(limit, 1.06),
                "התקרבות קלה בסיום, לפנייה ישירה לצופה")
    return (Action.ZOOM_IN.value, min(limit, 1.05),
            "שינוי מסגור קל כדי לשבור סטטיות")


# --------------------------------------------------------------------------
# הדגשות בכתוביות
# --------------------------------------------------------------------------
# יותר מזה והסרטון הופך לקרקס
MAX_EMPHASIS_PER_MINUTE = 6.0
MIN_EMPHASIS_GAP = 2.0


def _plan_emphasis(plan: VideoEditPlan, sem: SemanticAnalysis,
                   profile: PacingProfile) -> None:
    """
    בוחר אילו מילים להדגיש — ולא יותר מדי.

    כל מילה שנייה מודגשת שווה לאף מילה מודגשת. לכן מדגישים רק את
    המילה החזקה במשפטים בעלי תפקיד, עם מרווח מינימלי ביניהן ותקרה
    לדקה.
    """
    if not sem.sentences:
        return
    minutes = max(0.2, plan.source_duration / 60.0)
    budget = int(MAX_EMPHASIS_PER_MINUTE * minutes)
    if budget <= 0:
        return

    picks: list[tuple[float, Decision]] = []
    for s in sem.sentences:
        if s.role not in EMPHASIS_ROLES or not s.words:
            continue
        word = _strongest_word(s, sem.language or None)
        if word is None:
            continue
        score = s.confidence + (0.3 if s.role == "emotional_peak" else 0.0)
        picks.append((score, Decision(
            id=_mk_id("em"), action=Action.EMPHASIZE_WORD.value,
            start=word.start, end=word.end,
            reason=f"המילה החזקה ב{ROLE_LABELS_HE.get(s.role, s.role)}: "
                   f"„{word.text}”",
            confidence=min(0.9, s.confidence + 0.15),
            priority=Priority.NICE.value,
            params={"word": word.text, "role": s.role,
                    "sentence_index": s.index})))

    picks.sort(key=lambda p: -p[0])
    chosen: list[Decision] = []
    for _, d in picks:
        if len(chosen) >= budget:
            break
        if any(abs(d.start - c.start) < MIN_EMPHASIS_GAP for c in chosen):
            continue
        chosen.append(d)

    plan.captions = sorted(chosen, key=lambda d: d.start)
    if picks and len(plan.captions) < len(picks):
        plan.notes.append(
            f"נבחרו {len(plan.captions)} הדגשות מתוך {len(picks)} מועמדות — "
            "הדגשה על כל מילה שנייה מבטלת את האפקט.")


# מילים שאף פעם לא שוות הדגשה – מכל חבילות השפה (שם ישן, לתאימות)
def _all_emphasis_stop_words() -> set[str]:
    from . import lang as _lang

    return set().union(*(p.emphasis_stop_words for p in _lang.packs_for(None)))


_STOP_EMPHASIS = _all_emphasis_stop_words()


def _strongest_word(s: Sentence, language: Optional[str] = None):
    """
    המילה שנושאת את המשמעות במשפט.

    קודם מחפשים מילה שמופיעה בלקסיקון הרגשי או בהבטחת רשימה — אלה
    המילים שהמשפט נבנה סביבן. רק אם אין כזו נופלים לאורך, שהוא קירוב
    סביר אבל לא יותר מזה.
    """
    from . import lang as _lang

    packs = _lang.packs_for(language)
    stop = set().union(*(p.emphasis_stop_words for p in packs))
    fallback = None
    fallback_len = 0
    for w in s.words:
        tok = w.text.strip(".,!?;:\"'()[]…")
        low = tok.lower()
        if len(tok) < 3 or low in stop:
            continue
        for pack in packs:
            for term in tuple(pack.emotion) + tuple(pack.list_promises):
                if " " in term:
                    continue
                if pack.pattern(term).fullmatch(low):
                    return w
        if len(tok) > fallback_len:
            fallback, fallback_len = w, len(tok)
    return fallback


# --------------------------------------------------------------------------
# הצעות ויזואליות
# --------------------------------------------------------------------------
def _plan_broll(plan: VideoEditPlan, sem: SemanticAnalysis,
                profile: PacingProfile) -> None:
    """
    מציע נקודות שבהן ויזואל חיצוני מוסיף — ולא יותר מהתקציב.

    ההכרעה עצמה נעשית ב-`broll_engine`, שמחליט לכל משפט „חומר
    נלווה" או „הדובר" ומנמק. כאן רק ממירים את ההחלטות שלו
    להחלטות של התכנית.

    ההצעות אינן מיושמות: הן נכנסות כ-`suggest_broll` מושבתות,
    והמשתמש מחליט. כך גם אין כאן שום יצירה שעולה כסף.
    """
    if not plan.pacing or not sem.sentences:
        return
    from .broll_engine import plan_broll as decide

    aspect = "9:16" if plan.height > plan.width else "16:9"
    decisions = decide(sem, plan.pacing, aspect=aspect)

    for d in decisions.inserts:
        plan.broll.append(Decision(
            id=_mk_id("br"), action=Action.SUGGEST_BROLL.value,
            start=d.start, end=min(plan.source_end, d.start + d.duration),
            reason=d.reason,
            confidence=d.confidence,
            priority=Priority.NICE.value,
            params={"prompt": d.prompt, "query": d.query,
                    "aspect": aspect, "duration": d.duration,
                    "role": d.role, "category": d.category,
                    "concreteness": round(d.concreteness, 3),
                    "source": d.source, "asset_id": d.asset_id},
            requires_approval=True, enabled=False))

    plan.notes.extend(decisions.notes)
    if plan.broll:
        plan.notes.append(
            f"{len(plan.broll)} הצעות ויזואליות — אף אחת לא נוצרה ולא "
            "שובצה. יצירה ושיבוץ הן פעולות נפרדות של המשתמש.")
