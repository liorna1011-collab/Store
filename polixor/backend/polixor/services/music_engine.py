"""
מוזיקת רקע: עוצמה שמשתנה לפי הסיפור, והנמכה אוטומטית מתחת לדיבור.

**המערכת אינה מספקת מוזיקה.** אין כאן ספריית מוזיקה ואין הורדה
מאף מקור — המשתמש מביא קובץ משלו, והמנוע מערבב אותו נכון. זו
החלטה מכוונת: מוזיקה היא יצירה מוגנת, והכלי לא יכול להחליט
עבור המשתמש שיש לו זכות להשתמש בה.

שני דברים קורים כאן:

  הנמכה (ducking)  כשהדובר מדבר, המוזיקה יורדת. זה לא קיצוץ
                   בינארי אלא דחיסת sidechain: הקול עצמו מנהל
                   את עוצמת המוזיקה, ולכן ההנמכה נשמעת טבעית
                   ומתאוששת בהפסקות.
  עוצמה לפי Beat   מוזיקה שרצה באותה עוצמה לאורך כל הסרטון
                   משטיחה אותו. הפתיח מקבל עוצמה בינונית, גוף
                   הסיפור נמוכה, השיא הרגשי עולה, והסיום נסגר.

הבדיקה של השכבה הזו אינה „הפילטר נבנה" אלא **מדידה של המוזיקה
בפלט**: כמה dB היא יורדת מתחת לדיבור לעומת ההפסקות.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import i18n
from .semantics import SemanticAnalysis

log = logging.getLogger("polixor.music")


# --------------------------------------------------------------------------
@dataclass(frozen=True)
class MusicProfile:
    name: str
    # עוצמת המצע המוזיקלי ב-LUFS, לא הגבר קבוע. קובצי מוזיקה
    # מגיעים בעוצמות שונות לחלוטין, ולכן „הנמך ב-20dB" נותן
    # תוצאה אחרת לכל קובץ. יעד ב-LUFS נמדד מול הקובץ בפועל.
    bed_lufs: float
    duck_db: float        # כמה היא יורדת מתחת לדיבור
    attack_ms: int        # כמה מהר היא יורדת כשהדיבור מתחיל
    release_ms: int       # כמה מהר היא חוזרת בהפסקה

    @property
    def label(self) -> str:
        return i18n.tr(f"music.profile.{self.name}.label", default=self.name)

    @property
    def description(self) -> str:
        return i18n.tr(f"music.profile.{self.name}.description", default="")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label,
                "description": self.description, "bed_lufs": self.bed_lufs,
                "duck_db": self.duck_db}


PROFILES: dict[str, MusicProfile] = {
    # הדיבור ממוסטר ל--14 LUFS, ולכן היעדים כאן נמצאים 10–20dB
    # מתחתיו — הטווח המקובל למוזיקת רקע מתחת לדיאלוג.
    "minimal": MusicProfile(
        "minimal",
        bed_lufs=-34.0, duck_db=-12.0, attack_ms=25, release_ms=450),
    "balanced": MusicProfile(
        "balanced",
        bed_lufs=-29.0, duck_db=-15.0, attack_ms=20, release_ms=380),
    "energetic": MusicProfile(
        "energetic",
        bed_lufs=-25.0, duck_db=-17.0, attack_ms=15, release_ms=300),
}
DEFAULT_PROFILE = "balanced"

# §19: Hook → medium, Story → low, Emotional peak → rising, Ending → resolve
ROLE_GAIN_DB: dict[str, float] = {
    "hook": 0.0,             # בינוני — ברירת המחדל של הפרופיל
    "setup": -4.0,           # גוף הסיפור — נמוך
    "main_idea": -4.0,
    "key_claim": -5.0,       # טענה — הכי שקט, שהמילים יישמעו
    "tension": -1.5,
    "emotional_peak": +2.0,  # עולה
    "payoff": 0.0,
    "topic_change": -1.0,
    "cta": -2.0,
    "filler": -6.0,
}

# כשאי אפשר למדוד את קובץ המוזיקה, מניחים שהוא מנורמל בערך לרמה
# הזו. זו הנחה מוצהרת ולא מדידה, והיא נרשמת בהערות התכנית.
ASSUMED_MUSIC_LUFS = -16.0

FADE_IN = 1.2
FADE_OUT = 2.0
MIN_SEGMENT = 0.8
# מתחת להפרש הזה בין דיבור למוזיקה המילים מתחילות להיטשטש
MIN_SPEECH_HEADROOM_DB = 8.0


@dataclass
class MusicCue:
    start: float
    end: float
    gain_db: float
    role: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3),
                "gain_db": round(self.gain_db, 2), "role": self.role,
                "reason": self.reason}


@dataclass
class MusicPlan:
    profile: MusicProfile
    cues: list[MusicCue] = field(default_factory=list)
    duration: float = 0.0
    available: bool = False      # האם המשתמש סיפק קובץ מוזיקה
    music_lufs: Optional[float] = None   # עוצמת קובץ המוזיקה שנמדדה
    measured: bool = False               # האם המדידה באמת הצליחה
    notes: list[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.available and bool(self.cues)

    def gain_at(self, t: float) -> float:
        for c in self.cues:
            if c.start <= t < c.end:
                return c.gain_db
        return self.cues[-1].gain_db if self.cues else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "available": self.available,
            "active": self.is_active,
            "duration": round(self.duration, 3),
            "music_lufs": (round(self.music_lufs, 2)
                           if self.music_lufs is not None else None),
            "measured": self.measured,
            "cues": [c.to_dict() for c in self.cues],
            "notes": self.notes,
        }


def get_profile(name: Optional[str]) -> MusicProfile:
    return PROFILES.get((name or "").strip().lower(), PROFILES[DEFAULT_PROFILE])


def profile_catalog() -> list[dict[str, Any]]:
    return [p.to_dict() for p in PROFILES.values()]


# --------------------------------------------------------------------------
def plan_music(sem: Optional[SemanticAnalysis] = None, *,
               total_duration: float,
               profile: MusicProfile | str = DEFAULT_PROFILE,
               music_path: Optional[str | Path] = None,
               beats: Optional[list] = None) -> MusicPlan:
    """
    בונה עקומת עוצמה למוזיקה לפי מבנה הסיפור.

    `beats` מאפשר למסור ביטים ישירות — כמילונים עם `start`, `end`
    ו-`role`. זה מה שהפייפליין עושה: הביטים של הבמאי הם בזמני
    המקור, והם ממופים לזמני הפלט לפני שהם מגיעים לכאן. עקומה
    שנבנית על זמני המקור לא תתאים לקליפ שנחתך.

    בלי קובץ מוזיקה מוחזרת תכנית לא פעילה עם ההסבר — ולא עקומה
    שנראית כאילו משהו יקרה.
    """
    prof = profile if isinstance(profile, MusicProfile) else get_profile(profile)
    plan = MusicPlan(profile=prof, duration=max(0.0, total_duration))

    have = bool(music_path) and Path(str(music_path)).exists()
    plan.available = have
    if not have:
        plan.notes.append(i18n.tr("music.note.no_file"))
        return plan
    if total_duration <= 0:
        plan.notes.append(i18n.tr("music.note.no_duration"))
        plan.available = False
        return plan

    # מודדים את קובץ המוזיקה עצמו. בלי זה „הנמך ב-20dB" נותן
    # תוצאה שונה לגמרי לכל קובץ, ולהגדרה אין משמעות.
    plan.music_lufs, plan.measured = _measure_music(music_path)
    if not plan.measured:
        plan.notes.append(i18n.tr("music.note.unmeasured", lufs=f"{ASSUMED_MUSIC_LUFS:.0f}"))
    base = prof.bed_lufs - (plan.music_lufs or ASSUMED_MUSIC_LUFS)

    raw = beats if beats is not None else list(getattr(sem, "beats", None) or [])
    items = [_beat_view(b) for b in raw]
    items = [b for b in items if b is not None]
    if not items:
        plan.cues.append(MusicCue(
            0.0, total_duration, base - 4.0, "unknown",
            i18n.tr("music.cue.no_transcript")))
        plan.notes.append(i18n.tr("music.note.no_transcript"))
        _apply_ending(plan, prof)
        return plan

    for start, end, role in items:
        offset = ROLE_GAIN_DB.get(role, -3.0)
        start = max(0.0, start)
        end = min(total_duration, end)
        if end - start < MIN_SEGMENT:
            continue
        plan.cues.append(MusicCue(
            start, end, base + offset, role, _reason_for(role, offset)))

    if not plan.cues:
        plan.cues.append(MusicCue(0.0, total_duration, base - 4.0,
                                  "unknown", i18n.tr("music.cue.short")))
    else:
        _fill_gaps(plan, total_duration, base)
    _apply_ending(plan, prof)
    return plan


def _reason_for(role: str, offset: float) -> str:
    if role == "emotional_peak":
        return i18n.tr("music.cue.peak")
    if role == "hook":
        return i18n.tr("music.cue.hook")
    if role in ("key_claim", "cta"):
        return i18n.tr("music.cue.key")
    if role == "filler":
        return i18n.tr("music.cue.filler")
    if offset < 0:
        return i18n.tr("music.cue.body")
    return i18n.tr("music.cue.default")


def _beat_view(b) -> Optional[tuple[float, float, str]]:
    """מקבל ביט כאובייקט או כמילון ומחזיר (start, end, role)."""
    try:
        if isinstance(b, dict):
            return (float(b["start"]), float(b["end"]),
                    str(b.get("role") or "main_idea"))
        return (float(b.start), float(b.end),
                str(getattr(b, "role", "") or "main_idea"))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _measure_music(path) -> tuple[Optional[float], bool]:
    """עוצמת קובץ המוזיקה ב-LUFS, ודגל שאומר אם המדידה הצליחה."""
    try:
        from .audio_mastering import measure

        m = measure(path)
        if m.ok and m.lufs is not None:
            return float(m.lufs), True
    except Exception as exc:                            # noqa: BLE001
        log.debug("music measurement failed: %s", exc)
    return None, False


def _fill_gaps(plan: MusicPlan, total: float, base: float) -> None:
    """סוגר חורים בין ביטים כדי שהעקומה תכסה את כל הקליפ."""
    plan.cues.sort(key=lambda c: c.start)
    filled: list[MusicCue] = []
    cursor = 0.0
    for c in plan.cues:
        if c.start > cursor + 0.05:
            filled.append(MusicCue(cursor, c.start, base - 4.0,
                                   "gap", i18n.tr("music.cue.gap")))
        filled.append(c)
        cursor = max(cursor, c.end)
    if cursor < total - 0.05:
        filled.append(MusicCue(cursor, total, base - 4.0, "gap",
                               i18n.tr("music.cue.end")))
    plan.cues = filled


def _apply_ending(plan: MusicPlan, prof: MusicProfile) -> None:
    """§19: הסיום „נפתר" — המוזיקה נסגרת ולא נחתכת באמצע."""
    if not plan.cues:
        return
    plan.notes.append(i18n.tr("music.note.fades", fade_in=f"{FADE_IN:.1f}",
                              fade_out=f"{FADE_OUT:.1f}"))


# --------------------------------------------------------------------------
# בניית הפילטר
# --------------------------------------------------------------------------
def volume_expression(plan: MusicPlan) -> str:
    """
    ביטוי עוצמה תלוי-זמן לעקומה, כשרשרת `if` מקוננת.

    FFmpeg מעריך את הביטוי לכל פריים אודיו, ולכן הוא חייב להיות
    פונקציה של `t` בלבד. מספר הקטעים חסום בפועל במספר הביטים.
    """
    if not plan.cues:
        return "1.00000"
    expr = f"{_lin(plan.cues[-1].gain_db):.5f}"
    for c in reversed(plan.cues[:-1]):
        expr = f"if(lt(t,{c.end:.3f}),{_lin(c.gain_db):.5f},{expr})"
    return expr


def build_filter(plan: MusicPlan, *, music_input: int = 1,
                 voice_label: str = "0:a", out_label: str = "mix",
                 music_only: bool = False) -> str:
    """
    בונה `filter_complex` שמערבב מוזיקה מתחת לדיבור.

    `music_only=True` מחזיר רק את המוזיקה אחרי העיבוד, בלי הקול.
    זה מה שמאפשר **למדוד** את ההנמכה: משווים את עוצמת המוזיקה
    בזמן דיבור לעומת הפסקות, על אותו קובץ.

    ההנמכה היא `sidechaincompress` שהקול מנהל — לא חיתוך לפי
    זמנים. כך היא מגיבה לדיבור בפועל, כולל להפסקות קצרות.
    """
    p = plan.profile
    expr = volume_expression(plan)
    parts = [
        # המוזיקה: לולאה לאורך הקליפ, עוצמה לפי העקומה, פייד בכניסה וביציאה
        f"[{music_input}:a]aloop=loop=-1:size=2e9,atrim=0:{plan.duration:.3f},"
        f"asetpts=N/SR/TB,"
        f"volume=volume='{expr}':eval=frame,"
        f"afade=t=in:st=0:d={FADE_IN:.2f},"
        f"afade=t=out:st={max(0.0, plan.duration - FADE_OUT):.3f}"
        f":d={FADE_OUT:.2f}[mus]",
    ]

    # הקול מנהל את ההנמכה. בתערובת מלאה צריך אותו פעמיים — פעם
    # כמפתח ופעם בתוך התערובת — ולכן מפצלים. במצב מדידה הוא משמש
    # כמפתח בלבד, ופיצול היה משאיר יציאה לא מחוברת ש-FFmpeg פוסל.
    key = voice_label
    if not music_only:
        parts.append(f"[{voice_label}]asplit=2[vkey][vmix]")
        key = "vkey"

    parts.append(
        f"[mus][{key}]sidechaincompress="
        f"threshold={_lin(-30.0):.5f}:ratio={_duck_ratio(p.duck_db):.1f}:"
        f"attack={p.attack_ms}:release={p.release_ms}:makeup=1[ducked]")

    if music_only:
        parts.append(f"[ducked]anull[{out_label}]")
    else:
        parts.append(
            f"[vmix][ducked]amix=inputs=2:duration=first:normalize=0,"
            f"alimiter=limit=0.95:level=disabled[{out_label}]")
    return ";".join(parts)


def _duck_ratio(duck_db: float) -> float:
    """
    יחס דחיסה שמייצר בערך את ההנמכה המבוקשת.

    ‎-12dB ≈ יחס 6, ‎-15dB ≈ 9, ‎-17dB ≈ 12. היחס נגזר מהמרחק בין
    הסף לעוצמת הקול; הערכים כאן מכוילים לסף ‎-30dB ולקול מנורמל.
    """
    return float(max(2.0, min(20.0, abs(duck_db) * 0.7)))


def _lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


# --------------------------------------------------------------------------
# אימות
# --------------------------------------------------------------------------
@dataclass
class DuckingReport:
    ok: bool = False
    speech_db: Optional[float] = None
    pause_db: Optional[float] = None
    reduction_db: Optional[float] = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        def r(v):
            return None if v is None else round(float(v), 2)

        return {"ok": self.ok, "music_under_speech_db": r(self.speech_db),
                "music_in_pauses_db": r(self.pause_db),
                "reduction_db": r(self.reduction_db), "error": self.error}


def measure_ducking(music_only_wav: str | Path,
                    speech_spans: list[tuple[float, float]]) -> DuckingReport:
    """
    מודד בפועל כמה המוזיקה יורדת מתחת לדיבור.

    מקבל את פס המוזיקה **אחרי** העיבוד ובלי הקול, ואת הזמנים שבהם
    יש דיבור. מחזיר את ההפרש בדציבלים. זו הדרך היחידה לדעת
    שההנמכה באמת קרתה, ולא רק שהפילטר נבנה.
    """
    import wave

    import numpy as np

    report = DuckingReport()
    path = Path(music_only_wav)
    if not path.exists():
        report.error = i18n.tr("music.ducking.missing")
        return report
    try:
        with wave.open(str(path), "rb") as wf:
            sr = wf.getframerate()
            x = np.frombuffer(wf.readframes(wf.getnframes()),
                              dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as exc:                            # noqa: BLE001
        report.error = i18n.tr("music.ducking.read_failed", error=exc)
        return report
    if x.size == 0:
        report.error = i18n.tr("music.ducking.empty")
        return report

    mask = np.zeros(x.size, dtype=bool)
    for a, b in speech_spans:
        i0 = max(0, int(a * sr))
        i1 = min(x.size, int(b * sr))
        if i1 > i0:
            mask[i0:i1] = True

    # מתעלמים מהשוליים: שם הפייד משנה את העוצמה מסיבה אחרת
    edge = int(sr * max(FADE_IN, FADE_OUT))
    valid = np.zeros(x.size, dtype=bool)
    valid[edge: max(edge, x.size - edge)] = True

    speech = x[mask & valid]
    pause = x[(~mask) & valid]
    if speech.size < sr // 4 or pause.size < sr // 4:
        report.error = i18n.tr("music.ducking.not_enough")
        return report

    report.speech_db = _rms_db(speech)
    report.pause_db = _rms_db(pause)
    report.reduction_db = report.pause_db - report.speech_db
    report.ok = True
    return report


def _rms_db(x) -> float:
    import math

    import numpy as np

    rms = float(np.sqrt(np.mean(np.square(x))))
    return -120.0 if rms <= 1e-6 else float(20.0 * math.log10(rms))
