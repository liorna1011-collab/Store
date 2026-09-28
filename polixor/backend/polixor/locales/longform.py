"""טקסטים של מצב Long-Form: סיבות הסרה, הסברים ופרקים."""

NAMESPACE = "longform"

MESSAGES = {
    # ---- סיבות הסרה ----
    "reason.dead_air": {"he": "אוויר מת (שתיקה, היעדרות או AFK)",
                        "en": "Dead air (silence, away or AFK)"},
    "reason.off_topic": {"he": "לא קשור לנושא המרכזי", "en": "Not related to the main topic"},
    "reason.repetition": {"he": "חזרה על משהו שכבר נאמר", "en": "Repeats something already said"},
    "reason.low_interest": {"he": "עניין נמוך – לא נכנס בתקציב הזמן",
                            "en": "Low interest – did not fit the time budget"},
    "reason.filler": {"he": "מילות מילוי בלי תוכן", "en": "Filler words without content"},

    # ---- הסבר התכנית ----
    "explain.summary": {
        "he": "מתוך {source} נבחרו {output} ב-{parts} חלקים ו-{chapters} פרקים (יעד: {target}).",
        "en": "From {source}, {output} were kept in {parts} parts and {chapters} chapters (target: {target})."},
    "explain.removed": {"he": "הוסר – {reason}: {seconds}", "en": "Removed – {reason}: {seconds}"},
    "explain.short_source": {
        "he": "הסרטון קצר מהיעד: אחרי הסרת אוויר מת, חזרות וקטעים שלא קשורים לנושא נשאר רק {available} של תוכן.",
        "en": "The video is shorter than the target: after removing dead air, repetition and off-topic parts only {available} of content remained."},
    "explain.no_transcript": {
        "he": "אין תמלול, ולכן אין חלוקה לנושאים. הבחירה נעשתה לפי אותות אודיו ווידאו בלבד.",
        "en": "There is no transcript, so there are no topics. Selection used audio and video signals only."},
    "explain.section": {
        "he": "{start}–{end}: {title} (ציון {score})",
        "en": "{start}–{end}: {title} (score {score})"},
    "explain.chapters_note": {
        "he": "ל-YouTube נדרשים לפחות 3 פרקים של 10 שניות ומעלה; כאן יש {n}.",
        "en": "YouTube needs at least 3 chapters of 10 seconds or more; there are {n} here."},

    # ---- פרקים ----
    "chapter.fallback": {"he": "חלק {n}", "en": "Part {n}"},
    "chapter.intro": {"he": "פתיחה", "en": "Intro"},

    # ---- פייפליין ----
    "no_content": {
        "he": "לא נמצא תוכן מתאים לסרטון ארוך: כל המקור הוא אוויר מת או קטעים שהוסרו.",
        "en": "No suitable content for a long video: the whole source is dead air or removed parts."},
    "title": {"he": "{title} – גרסה ערוכה", "en": "{title} – edited version"},
    "title_default": {"he": "סרטון ערוך", "en": "Edited video"},
    "reason_text": {
        "he": "סרטון ארוך שנבנה מהנושאים המרכזיים בסדר כרונולוגי, בלי אוויר מת וחזרות.",
        "en": "A long video built from the main topics in chronological order, without dead air or repetition."},
}
