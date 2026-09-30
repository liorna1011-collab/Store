"""הסברים של מנוע בחירת הקליפים (services/clip_intel)."""

NAMESPACE = "clip_intel"

MESSAGES = {
    # ---- נימוק קצר שמוצג על הקליפ ----
    "reason": {
        "he": "וו: {hook} · פאנץ': {payoff} · ציון איכות {score}",
        "en": "Hook: {hook} · Payoff: {payoff} · quality score {score}"},
    "reason_none": {"he": "—", "en": "—"},

    # ---- למה הפתיחה טובה ----
    "why.hook_words": {"he": "ביטוי פתיחה חזק", "en": "a strong opening phrase"},
    "why.story_opener": {"he": "פתיחה של סיפור", "en": "opens a story"},
    "why.question": {"he": "שאלה שמעוררת סקרנות", "en": "a question that creates curiosity"},
    "why.clean_start": {"he": "מתחיל בתחילת רעיון (אחרי הפסקה או מעבר נושא)",
                        "en": "starts at the beginning of a thought (after a pause or topic change)"},
    "why.energetic": {"he": "פתיחה אנרגטית", "en": "an energetic opening"},
    "why.early_moment": {"he": "רגע חזק כבר בשניות הראשונות", "en": "a strong moment within the first seconds"},

    # ---- למה הפאנץ' חזק ----
    "why.reaction_after": {"he": "תגובה קולית מיד אחריו (צחוק/צעקה)",
                           "en": "a vocal reaction right after it (laughter/shouting)"},
    "why.reaction_words": {"he": "מילות תגובה („חחח”, „אין מצב”)",
                           "en": "reaction words (\"haha\", \"no way\")"},
    "why.payoff_marker": {"he": "תפנית או סיום („ובסוף”, „התברר ש…”)",
                          "en": "a twist or resolution (\"in the end\", \"turns out\")"},
    "why.emotion": {"he": "רגש חזק", "en": "strong emotion"},
    "why.energy_peak": {"he": "שיא עוצמה ביחס לסביבה", "en": "an energy peak compared to its surroundings"},
    "why.exclamation": {"he": "קריאה", "en": "an exclamation"},
    "why.strong_words": {"he": "מילים חזקות", "en": "strong words"},
    "why.followed_by_reaction": {"he": "אחריו משפט תגובה קצר", "en": "followed by a short reaction line"},
    "why.payoff_early": {"he": "הפאנץ' מוקדם מדי בקליפ", "en": "the payoff comes too early in the clip"},

    # ---- בעיות בפתיחה ----
    "problem.starts_mid_thought": {"he": "מתחיל באמצע מחשבה („אבל…”, „ולכן…”)",
                                   "en": "starts mid-thought (\"but…\", \"so then…\")"},
    "problem.needs_earlier_context": {"he": "מפנה למשהו שנאמר קודם", "en": "refers to something said earlier"},
    "problem.previous_sentence_unfinished": {"he": "המשפט הקודם לא הסתיים",
                                             "en": "the previous sentence is not finished"},
    "problem.starts_with_conclusion": {"he": "נפתח בסוף של סיפור („ובסוף…”, „התברר ש…”)",
                                       "en": "opens with the end of a story (\"in the end…\", \"turns out…\")"},
    "problem.filler_opening": {"he": "פתיחה עם מילות מילוי", "en": "opens with filler words"},

    # ---- רכיבי הציון ----
    "component.hook": {"he": "וו", "en": "Hook"},
    "component.payoff": {"he": "פאנץ'", "en": "Payoff"},
    "component.completeness": {"he": "עומד בפני עצמו", "en": "Stands on its own"},
    "component.arc": {"he": "בנייה אל השיא", "en": "Builds to the peak"},
    "component.density": {"he": "צפיפות דיבור", "en": "Speech density"},
    "component.signal": {"he": "אותות אודיו/וידאו", "en": "Audio/video signals"},

    # ---- קנסות ----
    "penalty.dead_air": {"he": "אוויר מת", "en": "Dead air"},
    "penalty.filler": {"he": "מילות מילוי", "en": "Filler words"},
    "penalty.starts_mid_thought": {"he": "מתחיל באמצע מחשבה", "en": "Starts mid-thought"},
    "penalty.needs_earlier_context": {"he": "תלוי בהקשר שלא בקליפ", "en": "Depends on context outside the clip"},
    "penalty.ends_mid_sentence": {"he": "נגמר באמצע משפט", "en": "Ends mid-sentence"},
    "penalty.off_topic": {"he": "תוכן לא רלוונטי (חסות, AFK, תקלות)",
                          "en": "Off-topic content (sponsor, AFK, technical issues)"},
    "penalty.no_setup": {"he": "מתחיל בפאנץ' עצמו, בלי הכנה", "en": "Starts with the payoff itself, with no setup"},
    "penalty.slow_middle": {"he": "הקשר ארוך מדי לתוכן", "en": "Too much context for the content"},
    "penalty.uncertain_transcript": {"he": "תמלול לא בטוח", "en": "Uncertain transcript"},
    "penalty.shorter_than_requested": {"he": "קצר מהאורך המבוקש", "en": "Shorter than requested"},

    # ---- סיבות דחייה ----
    "reject.no_payoff": {"he": "אין פאנץ' ברור – הקטע לא מגיע לשום מקום",
                         "en": "No clear payoff – the section doesn't lead anywhere"},
    "reject.weak_hook": {"he": "אין פתיחה שמחזיקה את הצופה", "en": "No opening that holds the viewer"},
    "reject.off_topic": {"he": "בעיקר תוכן לא רלוונטי", "en": "Mostly off-topic content"},
    "reject.below_quality_bar": {"he": "מתחת לרף האיכות ({score} < {threshold})",
                                 "en": "Below the quality bar ({score} < {threshold})"},
    "reject.over_limit": {"he": "עבר את הרף, אבל כבר נבחר המספר המרבי של קליפים",
                          "en": "Passed the bar, but the maximum number of clips was already chosen"},
    "reject.time_overlap": {"he": "חופף בזמן לקליפ חזק יותר", "en": "Overlaps a stronger clip in time"},
    "reject.same_content": {"he": "אותו תוכן כמו קליפ חזק יותר (דמיון {similarity})",
                            "en": "Same content as a stronger clip (similarity {similarity})"},

    # ---- גבולות ----
    "boundary.start.source_start": {"he": "תחילת הסרטון", "en": "Start of the video"},
    "boundary.start.topic_shift": {"he": "מעבר נושא", "en": "Topic change"},
    "boundary.start.after_pause": {"he": "אחרי הפסקה בדיבור", "en": "After a pause in speech"},
    "boundary.start.sentence_start": {"he": "תחילת משפט", "en": "Start of a sentence"},
    "boundary.end.closing_line": {"he": "אחרי משפט תגובה/סגירה קצר", "en": "After a short reaction/closing line"},
    "boundary.end.after_reaction": {"he": "אחרי התגובה לפאנץ'", "en": "After the reaction to the payoff"},
    "boundary.end.sentence_end": {"he": "סוף משפט", "en": "End of a sentence"},

    # ---- מקורות ההצעה ----
    "source.payoff_signal": {"he": "סימני פאנץ' בדיבור ובאודיו", "en": "Payoff cues in speech and audio"},
    "source.signal_peak": {"he": "שיא באותות אודיו/וידאו", "en": "A peak in audio/video signals"},
    "source.chat_spike": {"he": "קפיצה בצ'אט", "en": "A chat spike"},
    "source.llm": {"he": "הצעה של מודל השפה", "en": "Suggested by the language model"},

    # ---- הערות לריצה ----
    "note.summary": {
        "he": "ניתוח סיפורי: {selected} קליפים עברו את רף האיכות מתוך {stories} סיפורים אפשריים; {duplicates} כפילויות הוסרו.",
        "en": "Story analysis: {selected} clips passed the quality bar out of {stories} possible stories; {duplicates} duplicates were removed."},
    "note.none_passed": {
        "he": "אף רגע לא עבר את רף האיכות ({threshold}). {near} רגעים שכמעט עברו מופיעים בדוח הבחירה. אפשר להוריד את רף האיכות בהגדרות.",
        "en": "No moment passed the quality bar ({threshold}). {near} near misses are listed in the selection report. You can lower the quality bar in Settings."},
    "note.no_transcript": {
        "he": "בחירה לפי מבנה סיפור דורשת תמלול. הקליפים נבחרו לפי אותות אודיו ווידאו בלבד.",
        "en": "Story-based selection needs a transcript. The clips were chosen from audio and video signals only."},
}
