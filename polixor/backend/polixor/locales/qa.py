"""ממצאי בדיקת האיכות שאחרי הרינדור (render_qa)."""

NAMESPACE = "qa"

MESSAGES = {
    "summary.passed": {"he": "בדיקת איכות עברה ({count} בדיקות).", "en": "Quality check passed ({count} checks)."},
    "summary.errors": {"he": "{count} בעיות", "en": "{count} problems"},
    "summary.warnings": {"he": "{count} אזהרות", "en": "{count} warnings"},
    "summary.of": {"he": " מתוך {count} בדיקות", "en": " out of {count} checks"},

    "missing_output": {"he": "קובץ הפלט לא נוצר או ריק.", "en": "The output file was not created or is empty."},
    "unreadable": {"he": "לא ניתן לקרוא את קובץ הפלט: {error}", "en": "The output file cannot be read: {error}"},
    "no_video": {"he": "אין זרם וידאו בקובץ הפלט.", "en": "The output file has no video stream."},
    "no_audio": {"he": "המקור כלל פס קול, אבל בפלט אין זרם אודיו.",
                 "en": "The source had a soundtrack, but the output has no audio stream."},
    "bad_dimensions": {"he": "מידות לא תקינות: {width}×{height}", "en": "Invalid dimensions: {width}×{height}"},
    "duration_mismatch": {
        "he": "אורך הפלט {actual} שניות, התכנית הבטיחה {expected} — פער של {delta} שניות.",
        "en": "The output is {actual} seconds long, the plan promised {expected} – a gap of {delta} seconds."},
    "black_frames": {
        "he": "נמצאו {count} קטעים שחורים באמצע הסרטון ({seconds} שניות).",
        "en": "Found {count} black sections in the middle of the video ({seconds} seconds)."},
    "black_ratio": {
        "he": "{percent}% מהסרטון שחור — בדוק שהפייד לא ארוך מדי.",
        "en": "{percent}% of the video is black – check that the fade is not too long."},
    "frozen_error": {
        "he": "{percent}% מהסרטון קפוא ({seconds} שניות) — ייתכן שהרינדור נתקע או שחסר חומר.",
        "en": "{percent}% of the video is frozen ({seconds} seconds) – rendering may have stalled or material is missing."},
    "frozen_warning": {
        "he": "נמצאו {count} קטעים ללא תנועה ({seconds} שניות).",
        "en": "Found {count} sections without motion ({seconds} seconds)."},
    "frame_decode_failed": {
        "he": "{failed} מתוך {total} פריימים שנדגמו לא נפתחו.",
        "en": "{failed} of {total} sampled frames could not be decoded."},
    "all_frames_black": {
        "he": "כל הפריימים שנדגמו שחורים — אין תמונה בפלט.",
        "en": "Every sampled frame is black – the output has no picture."},
    "mostly_dark": {
        "he": "{dark} מתוך {total} פריימים כמעט שחורים.",
        "en": "{dark} of {total} frames are almost black."},
    "caption_out_of_bounds": {
        "he": "{count} כתוביות חורגות מאורך הקליפ (האחרונה מסתיימת ב-{last} שניות מתוך {duration}).",
        "en": "{count} captions run past the end of the clip (the last ends at {last} seconds of {duration})."},
    "caption_negative_start": {
        "he": "{count} כתוביות מתחילות לפני תחילת הקליפ.",
        "en": "{count} captions start before the clip begins."},
    "caption_outside_safe_area": {
        "he": "הכתובית יושבת {margin}px מהתחתית, והאזור השמור הוא {required}px — היא עלולה להיחתך על-ידי ממשק הפלטפורמה.",
        "en": "The caption sits {margin}px from the bottom and the reserved area is {required}px – the platform's interface may cover it."},

    "skipped.duration": {"he": "לא נמסר אורך צפוי מתכנית העריכה.", "en": "The edit plan gave no expected duration."},
    "skipped.blackdetect": {"he": "blackdetect לא זמין.", "en": "blackdetect is not available."},
    "skipped.freezedetect": {"he": "freezedetect לא זמין.", "en": "freezedetect is not available."},
    "skipped.too_short": {"he": "הקליפ קצר מדי לדגימה.", "en": "The clip is too short to sample."},
    "skipped.no_captions": {"he": "אין כתוביות בקליפ.", "en": "The clip has no captions."},
    "skipped.no_margin": {"he": "לא נמסרו שוליים לבדיקה.", "en": "No margins were given to check."},
}
