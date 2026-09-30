"""
חבילת שפה (LanguagePack) – כל מה שהניתוח צריך לדעת על שפה אחת.

הניתוח עצמו (סמנטיקה, ציון עניין, וו פתיחה, B-roll, שבירת שורות) אינו
מכיר עברית או אנגלית: הוא מקבל חבילה ושואל אותה. שפה חדשה = קובץ חדש
שמגדיר `LanguagePack` ורושם אותו, בלי לגעת במנועים.

התאמת ביטויים נעשית תמיד **בגבולות מילה**: „מת" לא נמצא בתוך „אמת",
ו-„lol" לא בתוך „lollipop". בשפות עם תחיליות דבוקות (עברית: ו, ה, ב,
ל, כ, מ, ש, כש) החבילה מגדירה `prefix_rule`, כך ש-„ניצחתי" נמצא גם
ב-„וניצחתי".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence


def default_normalizer(text: str) -> str:
    """אותיות קטנות, בלי ניקוד וסימני הטעמה, ורווחים מאוחדים."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class LanguagePack:
    code: str
    name: str
    direction: str = "ltr"                      # ltr | rtl
    # מחלקת תווים (גוף של [...] ב-regex) של אותיות המילה
    word_chars: str = r"\w'"
    # טווחי יוניקוד של הכתב – לזיהוי שפה מהטקסט
    script_ranges: tuple[tuple[int, int], ...] = ()
    # תחיליות דבוקות (רק בשפות שיש בהן), כ-regex אופציונלי לפני המילה
    prefix_rule: str = ""
    # אותן תחיליות כרשימה – להסרה לצורך השוואת מילים
    prefixes: tuple[str, ...] = ()
    # סיומות נטייה (רבים/נקבה) שמותרות אחרי המילה האחרונה בביטוי,
    # כך ש„סיפור" נמצא גם ב„סיפורים". חל רק על מילים של 3 אותיות ומעלה.
    suffix_rule: str = ""
    # מילות שלילה: „לא אהבתי" אינו ביטוי רגשי חיובי של „אהבתי"
    negations: frozenset[str] = frozenset()
    normalizer: Callable[[str], str] = default_normalizer

    stop_words: frozenset[str] = frozenset()
    filler_tokens: frozenset[str] = frozenset()
    filler_phrases: tuple[str, ...] = ()
    cta: tuple[str, ...] = ()
    hook: tuple[str, ...] = ()
    topic_shift: tuple[str, ...] = ()
    emotion: tuple[str, ...] = ()
    claim: tuple[str, ...] = ()
    # לקסיקון עניין משוקלל (0..1)
    lexicon: dict[str, float] = field(default_factory=dict)
    # קטגוריות רגע: key → ביטויים (התווית מגיעה מקטלוג התרגום)
    category_patterns: tuple[tuple[str, tuple[str, ...]], ...] = ()
    emphasis_stop_words: frozenset[str] = frozenset()
    number_words: tuple[str, ...] = ()
    list_promises: tuple[str, ...] = ()
    curiosity: tuple[str, ...] = ()
    throat_clearing: tuple[str, ...] = ()
    # מילים שלא ראוי שיסיימו שורת כתובית (נשענות על המילה הבאה)
    hanging_words: frozenset[str] = frozenset()
    # B-roll: מה שאפשר לצלם, ומה שמסמן הפשטה או דיבור על הסרטון
    places: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    motions: tuple[str, ...] = ()
    nature: tuple[str, ...] = ()
    abstract: tuple[str, ...] = ()
    meta: tuple[str, ...] = ()
    # לונג-פורם: היעדרות (AFK/BRB) וסטיות מהנושא (חסות, תקלות, „שנייה אני בא")
    afk: tuple[str, ...] = ()
    offtopic: tuple[str, ...] = ()
    # ---- מבנה סיפור לקליפים (services/clip_intel) ----
    # מילים שמשפט *מתחיל* בהן כשהוא ממשיך מחשבה קודמת („אבל", „ולכן").
    # קליפ שנפתח במשפט כזה מתחיל באמצע רעיון.
    continuation_starters: tuple[str, ...] = ()
    # הפניות להקשר שלא נמצא בקליפ („כמו שאמרתי", „הוא אמר ש…")
    backrefs: tuple[str, ...] = ()
    # סימני פאנץ'/פתרון/תפנית – „ובסוף", „התברר ש…", „turns out"
    payoff_markers: tuple[str, ...] = ()
    # תגובה – צחוק, קריאות („חחח", „אין מצב", „lol")
    reaction_tokens: tuple[str, ...] = ()
    # סגירה טבעית של רעיון („וזהו", „so yeah")
    closure_markers: tuple[str, ...] = ()
    # פתיחת סיפור („אז פעם אחת", „let me tell you")
    story_openers: tuple[str, ...] = ()
    # כינוי גוף שלישי בפתיחת משפט („הוא אמר…") – מפנה למישהו שהוזכר קודם
    dangling_pronouns: tuple[str, ...] = ()
    # שיחת חולין של שידור (ברכות, קריאת צ'אט, תודות) – לא תוכן לקליפ
    chitchat: tuple[str, ...] = ()
    # מחקר רחב יותר של תחיליות בהתאמת נכסים ("בכביש" ↔ "כביש")
    min_stem_after_prefix: int = 3

    _cache: dict[str, re.Pattern[str]] = field(default_factory=dict, repr=False)

    # ---- התאמה ----
    def pattern(self, phrase: str) -> re.Pattern[str]:
        """ביטוי רגולרי לביטוי מהלקסיקון, בגבולות מילה ועם תחילית אפשרית."""
        pat = self._cache.get(phrase)
        if pat is None:
            clean = phrase.strip()
            body = re.escape(clean)
            body = re.sub(r"(\\?\s)+", r"\\s+", body)
            words = clean.split() or [clean]
            # מילה קצרה לא מקבלת הרחבת תחילית: „הכי" היה נקרא כ-ה+„כי"
            prefix = self.prefix_rule if (self.prefix_rule and len(words[0]) > 2) else ""
            suffix = self.suffix_rule if (self.suffix_rule and len(words[-1]) > 2) else ""
            pat = re.compile(
                rf"(?<![{self.word_chars}]){prefix}{body}{suffix}(?![{self.word_chars}])",
                re.IGNORECASE)
            self._cache[phrase] = pat
        return pat

    def count(self, text: str, phrases: Sequence[str], *,
              skip_negated: bool = False) -> int:
        """
        כמה ביטויים מהרשימה מופיעים בטקסט (כל ביטוי נספר פעם אחת).

        `skip_negated`: התאמה שמיד לפניה מילת שלילה אינה נספרת.
        """
        low = self.normalizer(text)
        if not skip_negated or not self.negations:
            return sum(1 for p in phrases if self.pattern(p).search(low))
        n = 0
        for p in phrases:
            for m in self.pattern(p).finditer(low):
                before = low[:m.start()].split()
                if before and before[-1] in self.negations:
                    continue
                n += 1
                break
        return n

    def matched(self, text: str, phrases: Sequence[str]) -> list[str]:
        low = self.normalizer(text)
        return [p for p in phrases if self.pattern(p).search(low)]

    def strip_prefix(self, word: str) -> Optional[str]:
        low = (word or "").lower()
        for pref in sorted(self.prefixes, key=len, reverse=True):
            if low.startswith(pref) and len(low) - len(pref) >= self.min_stem_after_prefix:
                return low[len(pref):]
        return None

    def is_hanging(self, token: str) -> bool:
        bare = re.sub(r"[^\w']", "", (token or "").lower())
        if bare in self.hanging_words:
            return True
        # מילות הקישור קצרות („של", „את"), ולכן כאן מספיק גזע של 2 אותיות
        # („ושל", „ואת") – בשונה מ-strip_prefix שנזהר מגזעים קצרים
        for pref in sorted(self.prefixes, key=len, reverse=True):
            if bare.startswith(pref) and len(bare) - len(pref) >= 2 \
                    and bare[len(pref):] in self.hanging_words:
                return True
        return False

    # ---- זיהוי ----
    def script_ratio(self, text: str) -> float:
        """חלק האותיות בטקסט ששייכות לכתב של השפה."""
        letters = [c for c in text or "" if unicodedata.category(c).startswith("L")]
        if not letters or not self.script_ranges:
            return 0.0
        hits = sum(1 for c in letters
                   if any(lo <= ord(c) <= hi for lo, hi in self.script_ranges))
        return hits / len(letters)
