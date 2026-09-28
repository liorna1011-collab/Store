"""הצעות ויזואליות לקליפ: סוג הרמז והערות על מקור ההצעות."""

NAMESPACE = "suggest"

MESSAGES = {
    "cue.place": {"he": "תיאור מקום", "en": "A description of a place"},
    "cue.low": {"he": "רגע רגשי נמוך", "en": "An emotional low point"},
    "cue.high": {"he": "רגע שיא", "en": "A high point"},
    "cue.time": {"he": "קפיצה בזמן", "en": "A jump in time"},
    "cue.object": {"he": "אובייקט מוחשי", "en": "A tangible object"},
    "cue.concept": {"he": "רעיון מופשט", "en": "An abstract idea"},
    "cue.default": {"he": "משפט תיאורי", "en": "A descriptive sentence"},
    "llm_reason": {"he": "הצעת מודל", "en": "Model suggestion"},
    # השפה שבה המודל מתבקש לנמק את ההצעה
    "llm_reason_language": {"he": "בעברית", "en": "in English"},

    "note.no_transcript": {
        "he": "אין תמלול לקליפ הזה, ולכן אין בסיס להצעות. הפעל תמלול כדי לקבל הצעות ויזואליות.",
        "en": "This clip has no transcript, so there is no basis for suggestions. Turn on transcription to get visual suggestions."},
    "note.llm_unavailable": {"he": "מודל השפה לא היה זמין, ההצעות נוצרו במנוע המקומי.",
                             "en": "The language model was not available; the suggestions came from the local engine."},
    "note.llm_empty": {"he": "המודל לא מצא נקודות מתאימות; מוצגות הצעות מהמנוע המקומי.",
                       "en": "The model found no suitable points; suggestions from the local engine are shown."},
    "note.none": {"he": "לא נמצאו בתמלול משפטים שתמונה תחזק במיוחד.",
                  "en": "No sentences in the transcript would be notably strengthened by an image."},
}
