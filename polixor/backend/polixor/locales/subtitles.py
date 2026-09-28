"""טקסטים של מערכת הכתוביות: משפטי דוגמה לתצוגה מקדימה והודעות."""

NAMESPACE = "subtitles"

MESSAGES = {
    # משפט הדוגמה בתצוגה המקדימה – בשפת התוכן שנבחרה (לא בשפת הממשק).
    # בכוונה עם מילה לועזית, מספר וסימני פיסוק, כדי שהכיווניות תיבדק.
    "sample": {
        "he": "זה הרגע שבו הכול השתנה: 3 טעויות ב-YouTube שאף אחד לא מספר לכם עליהן!",
        "en": "This is the moment everything changed: 3 YouTube mistakes nobody tells you about!"},
    "preview_failed": {
        "he": "לא הצלחנו ליצור תצוגה מקדימה של הכתוביות.",
        "en": "Could not create a subtitle preview."},
    "background_source_frame": {
        "he": "הרקע הוא פריים מקובץ המקור בחיתוך מרכזי – המסגור הסופי של הקליפ עשוי להיות שונה.",
        "en": "The background is a frame from the source with a center crop – the clip's final framing may differ."},
    "background_plain": {
        "he": "רקע אחיד – אין עדיין קובץ מקור מקומי להצגה.",
        "en": "Plain background – there is no local source file to show yet."},
    "weight_synthetic": {
        "he": "משקל {weight} אינו מותקן בגופן הזה; libass ישתמש במשקל הקרוב ({actual}).",
        "en": "Weight {weight} is not installed for this font; libass will use the nearest weight ({actual})."},
}
