"""
Pacing Engine — כמה שינויים ויזואליים צריכים להיות, ואיפה.

הבעיה שהמנוע הזה פותר: קצב אחיד הורס סרטונים. סיפור רגשי שנחתך כל
שתי שניות מרגיש עצבני; סרטון ויראלי בלי שינוי ויזואלי במשך חצי דקה
מאבד את הצופה. הקצב הנכון תלוי גם בסגנון וגם ב**תפקיד** של הקטע:
אפילו בסרטון מהיר, השיא הרגשי מקבל אוויר.

המנוע מחזיר תקציב ויזואלי לכל קטע — לא פקודות. מי שמחליט מה בדיוק
לעשות עם התקציב הוא ה-Director.

Presets
-------
כל preset הוא סט פרמטרים על אותו מנוע, לא מנוע נפרד.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .. import i18n
from .semantics import NarrativeBeat

# --------------------------------------------------------------------------
# פרופילי קצב
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PacingProfile:
    """
    `visual_interval` הוא הטווח (מינימום, מקסימום) בשניות בין שני
    שינויים ויזואליים — חיתוך, שינוי זווית או ויזואל חיצוני.
    """

    name: str
    visual_interval: tuple[float, float]
    # כמה מהשינויים יהיו שינויי זווית (השאר: חיתוך או ויזואל)
    zoom_share: float = 0.5
    # כמה אגרסיבית ההסרה של אוויר מת
    silence_tolerance: float = 0.55      # שניות שקט שמותר להשאיר
    max_removed_ratio: float = 0.28
    # האם מותר להאיץ קטעים בלי דיבור
    allow_speedup: bool = False
    # תקציב ויזואלים חיצוניים (בי-רול/תמונות) לדקה
    broll_per_minute: float = 1.0
    # האם לשמור שתיקות דרמטיות בכל מחיר
    protect_pauses: bool = True

    # התווית וההסבר נקראים מהקטלוג בכל גישה (שפת הבקשה או הפרויקט).
    @property
    def label(self) -> str:
        return i18n.tr(f"director.pacing.{self.name}.label", default=self.name)

    @property
    def description(self) -> str:
        return i18n.tr(f"director.pacing.{self.name}.description", default="")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label,
            "description": self.description,
            "visual_interval": list(self.visual_interval),
            "zoom_share": self.zoom_share,
            "max_removed_ratio": self.max_removed_ratio,
            "broll_per_minute": self.broll_per_minute,
            "allow_speedup": self.allow_speedup,
        }


PROFILES: dict[str, PacingProfile] = {
    "clean_creator": PacingProfile(
        name="clean_creator",
        visual_interval=(3.0, 6.0), zoom_share=0.45,
        silence_tolerance=0.5, max_removed_ratio=0.26,
        broll_per_minute=0.8),
    "viral_short": PacingProfile(
        name="viral_short",
        visual_interval=(1.5, 3.0), zoom_share=0.6,
        silence_tolerance=0.3, max_removed_ratio=0.42,
        allow_speedup=True, broll_per_minute=2.4),
    "cinematic_story": PacingProfile(
        name="cinematic_story",
        visual_interval=(5.0, 9.0), zoom_share=0.7,
        silence_tolerance=0.9, max_removed_ratio=0.18,
        broll_per_minute=0.9),
    "podcast_clip": PacingProfile(
        name="podcast_clip",
        visual_interval=(3.5, 7.0), zoom_share=0.65,
        silence_tolerance=0.45, max_removed_ratio=0.30,
        broll_per_minute=0.6),
    "educational": PacingProfile(
        name="educational",
        visual_interval=(3.0, 5.5), zoom_share=0.4,
        silence_tolerance=0.5, max_removed_ratio=0.28,
        broll_per_minute=2.0),
    "product": PacingProfile(
        name="product",
        visual_interval=(2.5, 4.5), zoom_share=0.35,
        silence_tolerance=0.4, max_removed_ratio=0.34,
        broll_per_minute=3.0),
}

DEFAULT_PROFILE = "clean_creator"

# התאמה מסגנונות העריכה הישנים, כדי שהגדרות קיימות ימשיכו לעבוד
LEGACY_STYLE_MAP: dict[str, str] = {
    "raw": "clean_creator",
    "clean": "clean_creator",
    "dynamic": "podcast_clip",
    "hype": "viral_short",
}


def get_profile(name: str) -> PacingProfile:
    key = (name or "").strip().lower()
    if key in PROFILES:
        return PROFILES[key]
    mapped = LEGACY_STYLE_MAP.get(key)
    if mapped:
        return PROFILES[mapped]
    return PROFILES[DEFAULT_PROFILE]


def profile_catalog() -> list[dict[str, Any]]:
    return [p.to_dict() for p in PROFILES.values()]


# --------------------------------------------------------------------------
# מקדמים לפי תפקיד
# --------------------------------------------------------------------------
# מכפיל על המרווח בין שינויים. גדול מ-1 = איטי יותר, פחות שינויים.
ROLE_TEMPO: dict[str, float] = {
    "hook": 0.72,             # הפתיחה מהירה בכל סגנון
    "setup": 1.15,
    "main_idea": 1.0,
    "tension": 0.9,
    "emotional_peak": 1.45,   # השיא מקבל אוויר — אסור לקצוץ אותו לדקויות
    "payoff": 0.85,
    "key_claim": 0.95,
    "cta": 0.8,
    "topic_change": 0.75,     # מעבר נושא מצדיק שינוי ויזואלי
    "filler": 1.0,
}

# האם מותר להוסיף ויזואל חיצוני בתפקיד הזה
ROLE_ALLOWS_BROLL: dict[str, bool] = {
    "hook": False,            # בפתיחה רוצים את הפנים, לא תמונה
    "setup": True,
    "main_idea": True,
    "tension": True,
    "emotional_peak": False,  # ברגע הרגשי הצופה רוצה את הדובר
    "payoff": False,
    "key_claim": True,
    "cta": False,
    "topic_change": True,
    "filler": False,
}


# --------------------------------------------------------------------------
# תקציב לקטע
# --------------------------------------------------------------------------
@dataclass
class SectionPacing:
    """התקציב הוויזואלי של Beat אחד."""

    start: float
    end: float
    role: str
    interval: float             # שניות בין שינויים ויזואליים
    visual_changes: int         # כמה שינויים מתוכננים בקטע
    zoom_changes: int
    allow_broll: bool
    broll_slots: int
    reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3), "end": round(self.end, 3),
            "role": self.role, "interval": round(self.interval, 2),
            "visual_changes": self.visual_changes,
            "zoom_changes": self.zoom_changes,
            "allow_broll": self.allow_broll,
            "broll_slots": self.broll_slots,
            "reason": self.reason,
        }


@dataclass
class PacingPlan:
    profile: PacingProfile
    sections: list[SectionPacing] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_visual_changes(self) -> int:
        return sum(s.visual_changes for s in self.sections)

    @property
    def total_broll_slots(self) -> int:
        return sum(s.broll_slots for s in self.sections)

    def at(self, t: float) -> Optional[SectionPacing]:
        for s in self.sections:
            if s.start <= t <= s.end:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "sections": [s.to_dict() for s in self.sections],
            "total_visual_changes": self.total_visual_changes,
            "total_broll_slots": self.total_broll_slots,
            "notes": self.notes,
        }


def plan_pacing(beats: Sequence[NarrativeBeat], *,
                profile: PacingProfile | str = DEFAULT_PROFILE,
                total_duration: float = 0.0) -> PacingPlan:
    """
    בונה תקציב ויזואלי לכל Beat.

    ללא beats (למשל בלי תמלול) מוחזרת חלוקה אחידה לפי הפרופיל בלבד —
    עדיין שימושית, רק בלי ההתאמה לתפקיד.
    """
    prof = profile if isinstance(profile, PacingProfile) else get_profile(profile)
    plan = PacingPlan(profile=prof)

    if not beats:
        if total_duration > 0:
            lo, hi = prof.visual_interval
            interval = (lo + hi) / 2.0
            n = max(0, int(total_duration / interval) - 1)
            plan.sections.append(SectionPacing(
                start=0.0, end=total_duration, role="unknown",
                interval=interval, visual_changes=n,
                zoom_changes=int(round(n * prof.zoom_share)),
                allow_broll=False, broll_slots=0,
                reason=i18n.tr("director.pacing.no_transcript_reason")))
            plan.notes.append(i18n.tr("director.pacing.no_transcript_note"))
        return plan

    lo, hi = prof.visual_interval
    base = (lo + hi) / 2.0

    for b in beats:
        tempo = ROLE_TEMPO.get(b.role, 1.0)
        interval = max(lo * 0.8, min(hi * 1.6, base * tempo))
        # מספר השינויים: כמה מרווחים שלמים נכנסים בקטע
        changes = max(0, int(b.duration / interval))
        # קטע קצר מאוד לא מקבל שינוי בכלל — שינוי בתוך שנייה נראה כמו תקלה
        if b.duration < lo * 0.9:
            changes = 0
        zooms = int(round(changes * prof.zoom_share))

        allow = prof.broll_per_minute > 0 and ROLE_ALLOWS_BROLL.get(b.role, True)
        plan.sections.append(SectionPacing(
            start=b.start, end=b.end, role=b.role,
            interval=interval, visual_changes=changes, zoom_changes=zooms,
            allow_broll=allow, broll_slots=0,
            reason=_section_reason(b.role, tempo, allow)))

    _allocate_broll(plan, prof)
    _ensure_minimum_variety(plan, beats, prof)
    _reconcile_visual_changes(plan)
    return plan


def _reconcile_visual_changes(plan: PacingPlan) -> None:
    """
    כל שינוי ויזואלי שתוכנן חייב להתממש — כזום או כחומר נלווה.

    בלי זה `zoom_share` מתפקד כמסננת מאבדת: קטע עם שינוי אחד ו-45%
    זום מתעגל לאפס זומים, חומר נלווה חסום בתפקידים חזקים ממילא,
    והתוצאה היא תכנית שמבטיחה שינוי שלא קורה — סרטון סטטי שהקצב
    שלו „תוכנן".
    """
    for s in plan.sections:
        s.zoom_changes = max(0, s.visual_changes - s.broll_slots)


def _allocate_broll(plan: PacingPlan, prof: PacingProfile) -> None:
    """
    מחלק תקציב ויזואלים **גלובלי** בין הקטעים המתאימים.

    חישוב לכל קטע בנפרד מתאפס תמיד בסרטון קצר: קטע של ארבע שניות
    ב-2.4 ויזואלים לדקה נותן 0.16, שמתעגל לאפס. מכאן שהתקציב נגזר
    מאורך הסרטון כולו, ורק אז מחולק — וזו גם הצורה הנכונה לשלוט
    בעלות, כי מספר היצירות ידוע מראש.
    """
    eligible = [s for s in plan.sections if s.allow_broll]
    if not eligible or prof.broll_per_minute <= 0:
        return
    total = plan.sections[-1].end - plan.sections[0].start
    budget = int(round(total / 60.0 * prof.broll_per_minute))
    # סרטון קצר בסגנון שמשתמש בוויזואלים מקבל לפחות אחד
    if budget == 0 and prof.broll_per_minute >= 1.2 and total >= 10.0:
        budget = 1
    if budget <= 0:
        return

    # הקטעים הארוכים ביותר קודם: שם יש מקום לוויזואל בלי לדחוס
    order = sorted(eligible, key=lambda s: -s.duration)
    min_len = max(2.5, prof.visual_interval[0])
    given = 0
    for s in order:
        if given >= budget:
            break
        if s.duration < min_len:
            continue
        s.broll_slots = 1
        given += 1
    # אם נשאר תקציב, מוסיפים שני לקטעים הארוכים במיוחד
    for s in order:
        if given >= budget:
            break
        if s.duration >= min_len * 2.5:
            s.broll_slots += 1
            given += 1
    plan.notes.append(i18n.tr("director.pacing.budget", given=given, seconds=f"{total:.0f}",
                              per_minute=f"{prof.broll_per_minute:.1f}", style=prof.label))


def _ensure_minimum_variety(plan: PacingPlan, beats: Sequence[NarrativeBeat],
                            prof: PacingProfile) -> None:
    """
    סרטון שלם בלי שינוי ויזואלי אחד הוא תמונה סטטית, לא עריכה.

    בסגנון איטי על סרטון קצר החישוב יכול להתאפס לגמרי. אם הסרטון ארוך
    מספיק לפחות לשינוי אחד, נותנים אותו ל-Beat המשמעותי ביותר.
    """
    if plan.total_visual_changes > 0 or not plan.sections:
        return
    total = plan.sections[-1].end - plan.sections[0].start
    lo, _ = prof.visual_interval
    if total < lo * 1.6:
        plan.notes.append(i18n.tr("director.pacing.too_short"))
        return

    priority = ["emotional_peak", "payoff", "key_claim", "main_idea",
                "tension", "topic_change", "hook", "setup"]
    order = {r: i for i, r in enumerate(priority)}
    target = min(plan.sections,
                 key=lambda s: (order.get(s.role, 99), -s.duration))
    target.visual_changes = 1
    target.zoom_changes = 1 if prof.zoom_share >= 0.5 else 0
    target.reason += i18n.tr("director.pacing.single_change")
    plan.notes.append(i18n.tr("director.pacing.single_change_note", role=target.role))


def _section_reason(role: str, tempo: float, allow_broll: bool) -> str:
    """הסבר בעברית למה הקצב כאן מה שהוא. מוצג ב„למה ה-AI עשה את זה"."""
    if tempo <= 0.8:
        pace = i18n.tr("director.pacing.fast")
    elif tempo >= 1.3:
        pace = i18n.tr("director.pacing.slow")
    else:
        pace = i18n.tr("director.pacing.normal")

    key = f"director.pacing.why.{role}"
    why = i18n.tr(key) if i18n.has(key) else i18n.tr("director.pacing.why.default")

    tail = "" if allow_broll else i18n.tr("director.pacing.no_broll")
    return i18n.tr("director.pacing.reason", pace=pace, why=why, tail=tail)
