"""תוויות והודעות שהשרת מחזיר ישירות לממשק (הגדרות, מערכת, שידור חי, תמונות)."""

NAMESPACE = "system"

MESSAGES = {
    # ---- מצבי הקלטת שידור ----
    "live_state.idle": {"he": "ממתין", "en": "Waiting"},
    "live_state.detecting": {"he": "מזהה שידור…", "en": "Detecting stream…"},
    "live_state.connecting": {"he": "מתחבר…", "en": "Connecting…"},
    "live_state.live": {"he": "משדר", "en": "Live"},
    "live_state.reconnecting": {"he": "מתחבר מחדש…", "en": "Reconnecting…"},
    "live_state.stopping": {"he": "עוצר הקלטה…", "en": "Stopping recording…"},
    "live_state.completed": {"he": "ההקלטה הושלמה", "en": "Recording complete"},
    "live_state.failed": {"he": "נכשל", "en": "Failed"},

    # ---- תפקידי תמונה בקליפ ----
    "image_role.intro": {"he": "פתיח", "en": "Intro"},
    "image_role.outro": {"he": "סיום", "en": "Outro"},
    "image_role.insert": {"he": "הכנסה מלאה", "en": "Full insert"},
    "image_role.broll": {"he": "בי-רול", "en": "B-roll"},
    "image_role.overlay": {"he": "שכבה", "en": "Overlay"},
    "image_role.background": {"he": "רקע", "en": "Background"},
    "image_role.thumbnail": {"he": "תמונת שער", "en": "Thumbnail"},

    # ---- אנימציות כתוביות (קטלוג /edit/styles) ----
    "caption_anim.none.label": {"he": "ללא", "en": "None"},
    "caption_anim.none.description": {
        "he": "המילה הפעילה רק מחליפה צבע.",
        "en": "The active word only changes color."},
    "caption_anim.pop.label": {"he": "קפיצה", "en": "Pop"},
    "caption_anim.pop.description": {
        "he": "קפיצה קצרה של 8% שחוזרת לגודל המקורי.",
        "en": "A short 8% pop that returns to the original size."},
    "caption_anim.punch.label": {"he": "חזקה", "en": "Punch"},
    "caption_anim.punch.description": {
        "he": "קפיצה של 18% עם התעבות מתאר. לשורטים.",
        "en": "An 18% pop with a thicker outline. For shorts."},

    # ---- אזהרות מערכת ----
    "warn.ffmpeg": {
        "he": "FFmpeg לא נמצא – לא ניתן לעבד וידאו. הרץ scripts/install_windows.ps1 או התקן ידנית.",
        "en": "FFmpeg was not found – video cannot be processed. Run scripts/install_windows.ps1 or install it manually."},
    "warn.yt_dlp": {
        "he": "yt-dlp לא מותקן – ייבוא מקישור לא יעבוד. ניתן עדיין להעלות קבצים מהמחשב.",
        "en": "yt-dlp is not installed – importing from a link will not work. You can still upload files from your computer."},
    "warn.whisper": {
        "he": "faster-whisper לא מותקן – לא יהיה תמלול ולא כתוביות.",
        "en": "faster-whisper is not installed – there will be no transcription and no subtitles."},
    "warn.cv2": {
        "he": "OpenCV לא מותקן – אין ניתוח חזותי ואין מעקב פנים.",
        "en": "OpenCV is not installed – no visual analysis and no face tracking."},
    "warn.disk": {
        "he": "נותרו רק {free} פנויים בדיסק. עיבוד שידור ארוך עלול להיכשל.",
        "en": "Only {free} of disk space is left. Processing a long stream may fail."},

    # ---- בדיקת חיבור AI ----
    "ai_test.heuristic": {
        "he": "מצב היוריסטי מקומי – אין מה לבדוק, הוא תמיד זמין.",
        "en": "Local heuristic mode – nothing to test, it is always available."},
    "ai_test.ok": {"he": "החיבור תקין.", "en": "The connection works."},
    "ai_test.unexpected": {"he": "התקבלה תשובה לא צפויה.", "en": "An unexpected answer was received."},

    # ---- שידור חי ----
    "live.ffmpeg_note": {
        "he": "FFmpeg אינו זמין – לא ניתן להקליט שידור ללא FFmpeg.",
        "en": "FFmpeg is not available – a stream cannot be recorded without FFmpeg."},
    "live.ffmpeg_reason": {"he": "FFmpeg אינו מותקן.", "en": "FFmpeg is not installed."},
    "live.stop_pending": {
        "he": "אין הקלטה פעילה כרגע. הבקשה נרשמה ותיכנס לתוקף אם ההקלטה תתחיל.",
        "en": "No recording is active right now. The request was recorded and takes effect if recording starts."},
    "live.stop_ok": {
        "he": "העצירה התקבלה. המקטע הנוכחי נסגר ונשמר.",
        "en": "Stop received. The current segment was closed and saved."},

    # ---- תמונות ----
    "images.no_provider": {"he": "לא הוגדר ספק תמונות זמין.", "en": "No available image provider is configured."},
    "images.placements_failed": {"he": "טעינת שיבוצי התמונות נכשלה.", "en": "Loading the image placements failed."},
    "images.apply_failed": {"he": "שילוב התמונות נכשל: {error}", "en": "Inserting the images failed: {error}"},
}
