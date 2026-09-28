"""טקסטים של ייבוא מקישור: הערות לפלטפורמות ותוויות."""

NAMESPACE = "ingest"

MESSAGES = {
    "gdrive_note": {
        "he": "נדרש שיתוף המאפשר הורדה ('כל מי שיש לו הקישור'). קבצים פרטיים דורשים חיבור חשבון.",
        "en": "The file must be shared so that anyone with the link can download it. Private files need a signed-in account."},
    "kick_note": {
        "he": "תמיכת Kick ב-yt-dlp משתנה עם שינויים באתר. אם ההורדה נכשלת, ניתן להוריד ידנית ולהעלות את הקובץ.",
        "en": "Kick support in yt-dlp changes whenever the site changes. If the download fails, download the file manually and upload it."},
    "unknown_note": {
        "he": "הפלטפורמה אינה מזוהה במפורש. התוכנה תנסה לייבא דרך yt-dlp.",
        "en": "This platform is not recognised explicitly. Polixor will try to import it with yt-dlp."},
    "was_live_note": {
        "he": "זהו שידור שהסתיים – הוא ייובא כסרטון רגיל.",
        "en": "This stream has ended – it will be imported as a regular video."},
    "upcoming_note": {
        "he": "השידור טרם התחיל.",
        "en": "The stream has not started yet."},
    "direct_label": {"he": "קישור ישיר", "en": "Direct link"},
    "unknown_label": {"he": "לא ידוע", "en": "Unknown"},
    "platform.youtube": {"he": "YouTube", "en": "YouTube"},
    "platform.twitch": {"he": "Twitch", "en": "Twitch"},
    "platform.kick": {"he": "Kick", "en": "Kick"},
    "platform.gdrive": {"he": "Google Drive", "en": "Google Drive"},
    "platform.direct": {"he": "קישור ישיר", "en": "Direct link"},
    "platform.upload": {"he": "קובץ מהמחשב", "en": "Uploaded file"},
    "platform.other": {"he": "אתר אחר", "en": "Other site"},
    "content.video": {"he": "סרטון", "en": "Video"},
    "content.live": {"he": "שידור חי", "en": "Live stream"},
    "content.clip": {"he": "קליפ", "en": "Clip"},
    "content.short": {"he": "Short", "en": "Short"},
}
