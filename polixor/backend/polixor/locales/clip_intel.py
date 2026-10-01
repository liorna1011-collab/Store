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
    "why.conflict": {"he": "ויכוח או אי־הסכמה", "en": "a disagreement or argument"},
    "why.opinion": {"he": "דעה חזקה", "en": "a strong opinion"},
    "why.meaningful_question": {"he": "שאלה אמיתית שמחכה לתשובה", "en": "a real question waiting for an answer"},
    "why.story": {"he": "פתיחה של סיפור", "en": "opens a story"},
    "why.claim": {"he": "טענה מפתיעה", "en": "a surprising claim"},
    "why.comparison": {"he": "השוואה או דירוג", "en": "a comparison or ranking"},

    # ---- למה הפאנץ' חזק ----
    "why.reaction_after": {"he": "תגובה קולית מיד אחריו (צחוק/צעקה)",
                           "en": "a vocal reaction right after it (laughter/shouting)"},
    "why.reaction_words": {"he": "מילות תגובה („חחח”, „אין מצב”)",
                           "en": "reaction words (\"haha\", \"no way\")"},
    "why.payoff_marker": {"he": "תפנית, סיום או תובנה („ובסוף”, „התברר ש…”, „למדתי”)",
                          "en": "a twist, resolution or insight (\"in the end\", \"turns out\", \"I learned\")"},
    "why.emotion": {"he": "רגש חזק", "en": "strong emotion"},
    "why.energy_peak": {"he": "שיא עוצמה ביחס לסביבה", "en": "an energy peak compared to its surroundings"},
    "why.exclamation": {"he": "קריאה", "en": "an exclamation"},
    "why.strong_words": {"he": "מילים חזקות", "en": "strong words"},
    "why.followed_by_reaction": {"he": "אחריו משפט תגובה קצר", "en": "followed by a short reaction line"},
    "why.payoff_early": {"he": "הפאנץ' מוקדם מדי בקליפ", "en": "the payoff comes too early in the clip"},
    "why.verdict": {"he": "הכרעה או שורה תחתונה", "en": "a verdict or bottom line"},
    "why.answers_question": {"he": "עונה על השאלה שנשאלה בקליפ", "en": "answers the question asked in the clip"},
    "why.acoustic_only": {"he": "רק תגובה קולית, בלי תוכן במילים", "en": "only a vocal reaction, with nothing said"},

    # ---- בעיות בפתיחה ----
    "problem.starts_mid_thought": {"he": "מתחיל באמצע מחשבה („אבל…”, „ולכן…”)",
                                   "en": "starts mid-thought (\"but…\", \"so then…\")"},
    "problem.needs_earlier_context": {"he": "מפנה למשהו שנאמר קודם", "en": "refers to something said earlier"},
    "problem.previous_sentence_unfinished": {"he": "המשפט הקודם לא הסתיים",
                                             "en": "the previous sentence is not finished"},
    "problem.starts_with_conclusion": {"he": "נפתח בסוף של סיפור („ובסוף…”, „התברר ש…”)",
                                       "en": "opens with the end of a story (\"in the end…\", \"turns out…\")"},
    "problem.unresolved_reference": {"he": "נפתח בכינוי („הוא”, „היא”) בלי שברור על מי מדובר",
                                     "en": "opens with a pronoun (\"he\", \"she\") without saying who"},
    "problem.misses_the_question": {"he": "נפתח בתשובה – השאלה נשארה מחוץ לקליפ",
                                    "en": "opens with an answer – the question was left out"},
    "problem.filler_opening": {"he": "פתיחה עם מילות מילוי", "en": "opens with filler words"},
    "problem.trivial_question": {"he": "נפתח בשאלה שגרתית („איזה יום היום?”)",
                                 "en": "opens with a routine question (\"what day is it?\")"},
    "problem.private_talk": {"he": "פנייה פרטית למישהו מחוץ לשידור", "en": "private talk to someone off-stream"},
    "problem.unclear_opening": {"he": "הפתיחה לא ברורה (תמלול לא בטוח)", "en": "unclear opening (uncertain transcript)"},

    # ---- רכיבי הציון ----
    "component.hook": {"he": "וו", "en": "Hook"},
    "component.payoff": {"he": "פאנץ'", "en": "Payoff"},
    "component.completeness": {"he": "עומד בפני עצמו", "en": "Stands on its own"},
    "component.arc": {"he": "בנייה אל השיא", "en": "Builds to the peak"},
    "component.density": {"he": "צפיפות דיבור", "en": "Speech density"},
    "component.signal": {"he": "אותות אודיו/וידאו", "en": "Audio/video signals"},
    "component.judge": {"he": "הערכת מודל השפה", "en": "Language-model rating"},
    "component.visual": {"he": "פעילות על המסך", "en": "On-screen activity"},

    # ---- קנסות ----
    "penalty.dead_air": {"he": "אוויר מת", "en": "Dead air"},
    "penalty.filler": {"he": "מילות מילוי", "en": "Filler words"},
    "penalty.starts_mid_thought": {"he": "מתחיל באמצע מחשבה", "en": "Starts mid-thought"},
    "penalty.needs_earlier_context": {"he": "תלוי בהקשר שלא בקליפ", "en": "Depends on context outside the clip"},
    "penalty.ends_mid_sentence": {"he": "נגמר באמצע משפט", "en": "Ends mid-sentence"},
    "penalty.off_topic": {"he": "תוכן לא רלוונטי (חסות, AFK, תקלות)",
                          "en": "Off-topic content (sponsor, AFK, technical issues)"},
    "penalty.no_setup": {"he": "מתחיל בפאנץ' עצמו, בלי הכנה", "en": "Starts with the payoff itself, with no setup"},
    "penalty.black_or_frozen_video": {"he": "מסך שחור או תמונה קפואה", "en": "Black or frozen picture"},
    "penalty.unresolved_reference": {"he": "לא ברור על מי מדובר", "en": "Unclear who is being talked about"},
    "penalty.misses_the_question": {"he": "חסרה השאלה שהקליפ עונה עליה", "en": "Missing the question it answers"},
    "penalty.ends_before_peak": {"he": "נגמר לפני הרגע החזק", "en": "Ends before the strongest moment"},
    "penalty.ordinary_conversation": {"he": "בעיקר שיחה שגרתית", "en": "Mostly ordinary conversation"},
    "penalty.slow_middle": {"he": "הקשר ארוך מדי לתוכן", "en": "Too much context for the content"},
    "penalty.uncertain_transcript": {"he": "תמלול לא בטוח", "en": "Uncertain transcript"},
    "penalty.shorter_than_requested": {"he": "קצר מהאורך המבוקש", "en": "Shorter than requested"},
    "penalty.slow_start": {"he": "לוקח זמן עד שקורה משהו", "en": "Takes too long to get going"},
    "penalty.private_talk": {"he": "שיחה פרטית מחוץ לשידור", "en": "Private talk off-stream"},
    "penalty.unclear_transcript": {"he": "קטעים לא ברורים בדיבור", "en": "Unclear speech"},
    "penalty.topic_jump": {"he": "קופץ בין נושאים", "en": "Jumps between topics"},

    # ---- סיבות דחייה ----
    "reject.no_payoff": {"he": "אין פאנץ' ברור – הקטע לא מגיע לשום מקום",
                         "en": "No clear payoff – the section doesn't lead anywhere"},
    "reject.acoustic_only_payoff": {"he": "השיא הוא רק צעקה או תגובה קולית – אין בו תוכן",
                                    "en": "The peak is only shouting or a vocal reaction – nothing is said"},
    "reject.private_talk": {"he": "שיחה פרטית עם מישהו מחוץ לשידור", "en": "Private talk with someone off-stream"},
    "reject.ends_before_answer": {"he": "נגמר לפני התשובה", "en": "Ends before the answer"},
    "reject.topic_jump": {"he": "קופץ באמצע לנושא אחר", "en": "Jumps to another topic halfway"},
    "reject.slow_start": {"he": "הוו מגיע מאוחר מדי", "en": "The hook comes too late"},
    "reject.too_short": {"he": "קצר מדי בשביל קליפ", "en": "Too short for a clip"},
    "reject.unclear_opening": {"he": "הפתיחה לא ברורה ואין בה סיבה להמשיך לצפות",
                               "en": "Unclear opening with no reason to keep watching"},
    "reject.weak_hook": {"he": "אין פתיחה שמחזיקה את הצופה", "en": "No opening that holds the viewer"},
    "reject.off_topic": {"he": "בעיקר תוכן לא רלוונטי", "en": "Mostly off-topic content"},
    "reject.ordinary_conversation": {"he": "שיחה שגרתית – אין כאן רגע ששווה קליפ",
                                     "en": "Ordinary conversation – no moment worth a clip"},
    "reject.judge": {"he": "מודל השפה פסל: {reason}", "en": "Rejected by the language model: {reason}"},
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
    "boundary.end.answer_included": {"he": "הוארך עד התשובה", "en": "Extended to include the answer"},
    "boundary.end.verdict_included": {"he": "הוארך עד השורה התחתונה", "en": "Extended to include the bottom line"},

    # ---- מקורות ההצעה ----
    "source.payoff_signal": {"he": "סימני פאנץ' בדיבור ובאודיו", "en": "Payoff cues in speech and audio"},
    "source.signal_peak": {"he": "שיא באותות אודיו/וידאו", "en": "A peak in audio/video signals"},
    "source.chat_spike": {"he": "קפיצה בצ'אט", "en": "A chat spike"},
    "source.llm": {"he": "הצעה של מודל השפה", "en": "Suggested by the language model"},
    "source.argument": {"he": "ויכוח או החלפת דעות", "en": "An argument or exchange of opinions"},
    "source.judge": {"he": "גבולות שהעורך הציע (נבדקו מחדש)", "en": "Boundaries suggested by the editor (re-checked)"},
    "source.topic": {"he": "הרגע החזק בנושא", "en": "The strongest moment of a topic"},

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
