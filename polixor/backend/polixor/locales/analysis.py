"""
הסברים, תוויות וכותרות גיבוי של הניתוח.

כלל השפה: תוויות, נימוקים והסברים בשפת הממשק. כותרות שנגזרות
מהתוכן (כולל כותרת גיבוי) בשפת הדיבור.
"""

NAMESPACE = "analysis"

MESSAGES = {
    "speakers_unavailable": {
        "he": "זיהוי דוברים (מי מדבר מתי) אינו ממומש ב-Polixor, ולכן לא מוצג מספר דוברים.",
        "en": "Speaker detection (who speaks when) is not implemented in Polixor, so no speaker count is shown."},

    # ---- קטגוריות רגע ----
    "category.funny": {"he": "רגע מצחיק", "en": "Funny moment"},
    "category.win": {"he": "ניצחון או הצלחה", "en": "Win or success"},
    "category.fail": {"he": "כישלון או תסכול", "en": "Fail or frustration"},
    "category.surprise": {"he": "הפתעה", "en": "Surprise"},
    "category.story": {"he": "סיפור או גילוי", "en": "Story or reveal"},
    "category.argument": {"he": "ויכוח או עמדה חדה", "en": "Argument or strong opinion"},
    "category.highlight": {"he": "רגע שיא", "en": "Highlight"},
    "category.visual": {"he": "רגע ויזואלי בולט", "en": "Striking visual moment"},
    "category.moment": {"he": "רגע בולט בשידור", "en": "Notable moment"},

    # ---- אותות ----
    "signal.vocal": {"he": "עוצמת קול ושינוי אודיו", "en": "Voice intensity and audio change"},
    "signal.speech": {"he": "תוכן הדיבור", "en": "What was said"},
    "signal.visual": {"he": "שינוי ויזואלי", "en": "Visual change"},
    "signal.pause": {"he": "שתיקה דרמטית", "en": "Dramatic pause"},
    "signal.chat": {"he": "פעילות בצ'אט", "en": "Chat activity"},
    "signal_desc.vocal": {"he": "שינוי חד בעוצמת הקול", "en": "A sharp change in voice intensity"},
    "signal_desc.visual": {"he": "שינוי ויזואלי בולט", "en": "A striking visual change"},
    "signal_desc.pause": {"he": "שתיקה ואחריה דיבור", "en": "Silence followed by speech"},
    "signal_desc.speech": {"he": "דיבור צפוף", "en": "Dense speech"},
    "signal_desc.chat": {"he": "פעילות צ'אט גבוהה", "en": "High chat activity"},

    # ---- תיאור ונימוק של מועמד ----
    "said": {"he": "נאמר: “{text}”", "en": "Said: “{text}”"},
    "no_transcript_here": {"he": "אין תמלול לקטע זה", "en": "No transcript for this part"},
    "reason_default": {"he": "נבחר לפי ציון עניין משולב.", "en": "Chosen by the combined interest score."},
    "reason_signals": {"he": "אותות מובילים: {signals}.", "en": "Leading signals: {signals}."},
    "reason_lexical": {"he": " נמצאו גם ביטויים המעידים על רגע בולט בתוכן.",
                       "en": " Phrases that signal a notable moment were also found."},

    # ---- כותרות גיבוי (בשפת הדיבור) ----
    "title_at": {"he": "רגע בולט בדקה {stamp}", "en": "Notable moment at {stamp}"},
    "title_moment": {"he": "רגע מהשידור", "en": "Moment from the stream"},
    "highlights_title": {"he": "מיטב הרגעים – {n} קטעים", "en": "Best moments – {n} parts"},
    "highlights_desc": {"he": "סרטון מקבץ של {n} רגעים מהשידור", "en": "A compilation of {n} moments from the stream"},
    "highlights_reason": {"he": "שילוב {n} הרגעים עם ציון העניין הגבוה ביותר בשידור, בסדר כרונולוגי.",
                          "en": "The {n} moments with the highest interest score in the stream, in chronological order."},

    # ---- תפקידים נרטיביים ----
    "role.hook": {"he": "וו פתיחה", "en": "Hook"},
    "role.setup": {"he": "הקשר", "en": "Setup"},
    "role.main_idea": {"he": "הרעיון המרכזי", "en": "Main idea"},
    "role.tension": {"he": "מתח", "en": "Tension"},
    "role.emotional_peak": {"he": "שיא רגשי", "en": "Emotional peak"},
    "role.payoff": {"he": "פאנץ׳", "en": "Payoff"},
    "role.key_claim": {"he": "טענה מרכזית", "en": "Key claim"},
    "role.cta": {"he": "קריאה לפעולה", "en": "Call to action"},
    "role.topic_change": {"he": "מעבר נושא", "en": "Topic change"},
    "role.filler": {"he": "מילוי", "en": "Filler"},

    # ---- נימוקי תפקיד ----
    "why.hook_open": {"he": "פותח את הסרטון — שלוש השניות שמחזיקות את הצופה",
                      "en": "Opens the video — the three seconds that hold the viewer"},
    "why.hook_phrase": {"he": "ניסוח פתיחה חזק", "en": "A strong opening phrase"},
    "why.cta": {"he": "מכיל קריאה לפעולה", "en": "Contains a call to action"},
    "why.topic_change": {"he": "ביטוי מעבר לנושא חדש", "en": "A phrase that moves to a new topic"},
    "why.emotion_energy": {"he": "עוצמה גבוהה ({energy}) עם ניסוח רגשי",
                           "en": "High intensity ({energy}) with emotional wording"},
    "why.emotion_text": {"he": "ניסוח רגשי חזק (אין פס קול לנתח את העוצמה)",
                         "en": "Strong emotional wording (no soundtrack to measure intensity)"},
    "why.key_claim": {"he": "מנמק או מסביר — משפט שנושא את המסר",
                      "en": "Explains or argues — a sentence that carries the message"},
    "why.tension": {"he": "שאלה או השהיה שמייצרת ציפייה",
                    "en": "A question or pause that builds anticipation"},
    "why.payoff": {"he": "בחלק האחרון, אחרי בניית המתח", "en": "In the last part, after the tension builds"},
    "why.filler": {"he": "{ratio} מילות מילוי, בלי תוכן חדש", "en": "{ratio} filler words, no new content"},
    "why.setup": {"he": "בונה הקשר לפני הרעיון המרכזי", "en": "Builds context before the main idea"},
    "why.body": {"he": "חלק מגוף הסרטון", "en": "Part of the body of the video"},
    "why.late_hook": {"he": "ניסוח חזק, אבל מאוחר מכדי לשמש וו פתיחה",
                      "en": "Strong wording, but too late to serve as a hook"},
    "why.second_hook_filler": {"he": "מילות מילוי בפתיחה — אפשר לחתוך כדי להגיע מהר יותר לעניין",
                               "en": "Filler at the start — can be cut to get to the point faster"},
    "why.second_hook": {"he": "מוקדם בסרטון, אבל הוו כבר נתפס במשפט שלפניו",
                        "en": "Early in the video, but the hook is already the previous sentence"},
    "why.first_content": {"he": "המשפט הראשון בעל תוכן — משמש כוו פתיחה בפועל",
                          "en": "The first sentence with content — acts as the hook"},
    "why.not_peak": {"he": "עוצמה גבוהה, אבל לא השיא של הסרטון",
                     "en": "High intensity, but not the peak of the video"},
    "why.early_cta": {"he": "ניסוח של קריאה לפעולה, אבל מוקדם מדי בסרטון",
                      "en": "Call-to-action wording, but too early in the video"},
    "semantics.no_transcript": {
        "he": "אין תמלול לקטע הזה, ולכן אין ניתוח סמנטי. העריכה תתבסס על אותות אודיו ווידאו בלבד.",
        "en": "There is no transcript for this part, so there is no semantic analysis. Editing will use audio and video signals only."},

    # ---- סיווג תפקידים במודל שפה ----
    "llm_roles.unavailable": {
        "he": "מודל השפה לא היה זמין; הסיווג נעשה במנוע המקומי.",
        "en": "The language model was not available; classification was done by the local engine."},
    "llm_roles.invalid": {
        "he": "מודל השפה החזיר תשובה פסולה; נעשה שימוש במנוע המקומי.",
        "en": "The language model returned an invalid answer; the local engine was used."},
    "llm_roles.unusable": {
        "he": "מודל השפה לא החזיר סיווג שמיש; נעשה שימוש במנוע המקומי.",
        "en": "The language model did not return a usable classification; the local engine was used."},
}
