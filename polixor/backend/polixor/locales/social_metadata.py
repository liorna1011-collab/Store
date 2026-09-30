"""מטא-דאטה לרשתות (services/social_metadata)."""

NAMESPACE = "social_metadata"

MESSAGES = {
    "rules_note": {
        "he": "אין מודל שפה מחובר, ולכן ההצעה מבוססת על כותרת הקליפ והתמלול שלו. חברו מודל בהגדרות → AI להצעות מלאות לכל פלטפורמה.",
        "en": "No language model is connected, so the suggestion is based on the clip's title and transcript. Connect one in Settings → AI for full per-platform suggestions."},
    "ai_failed": {
        "he": "מודל השפה לא החזיר הצעה תקינה, ולכן ההצעה מבוססת על הכותרת והתמלול. אפשר לנסות שוב.",
        "en": "The language model didn't return a valid suggestion, so this one is based on the title and transcript. You can try again."},
    "no_transcript": {
        "he": "לקליפ הזה אין תמלול, ולכן ההצעה מבוססת רק על הכותרת.",
        "en": "This clip has no transcript, so the suggestion is based on the title only."},
}
