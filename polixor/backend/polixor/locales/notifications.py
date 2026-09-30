"""התראות (services/notifications). כל סוג: title + body."""

NAMESPACE = "notifications"

MESSAGES = {
    "group.needs_attention": {"he": "דורש טיפול", "en": "Needs attention"},
    "group.new": {"he": "חדש", "en": "New"},
    "group.earlier": {"he": "קודם", "en": "Earlier"},

    "analysis_complete.title": {"he": "הניתוח הסתיים", "en": "Analysis complete"},
    "analysis_complete.body": {
        "he": "„{project}” מוכן לבחירת הגדרות וליצירת קליפים.",
        "en": "“{project}” is ready for settings and clip generation."},

    "clips_ready.title": {"he": "הקליפים מוכנים", "en": "Clips ready"},
    "clips_ready.body": {
        "he": "{clips} קליפים מוכנים ב„{project}”.",
        "en": "{clips} clips are ready in “{project}”."},

    "clips_ready_review.title": {"he": "הקליפים מוכנים – חלקם דורשים בדיקה",
                                 "en": "Clips ready – some need review"},
    "clips_ready_review.body": {
        "he": "{clips} קליפים מוכנים ב„{project}”; {review} מהם סומנו לבדיקה לפני פרסום.",
        "en": "{clips} clips are ready in “{project}”; {review} were flagged for review before publishing."},

    "no_clips.title": {"he": "העיבוד הסתיים בלי קליפים", "en": "Finished with no clips"},
    "no_clips.body": {
        "he": "באף רגע ב„{project}” לא היה מספיק תוכן לקליפ. דוח הבחירה מראה מה כמעט עבר.",
        "en": "No moment in “{project}” was strong enough for a clip. The selection report shows the near misses."},

    "render_complete.title": {"he": "הייצוא הסתיים", "en": "Export complete"},
    "render_complete.body": {
        "he": "„{clip}” יוצא מחדש ומוכן.",
        "en": "“{clip}” was exported again and is ready."},

    "processing_failed.title": {"he": "העיבוד נכשל", "en": "Processing failed"},
    "processing_failed.body": {"he": "„{project}”:", "en": "“{project}”:"},

    "render_failed.title": {"he": "הייצוא נכשל", "en": "Export failed"},
    "render_failed.body": {"he": "„{clip}”:", "en": "“{clip}”:"},

    "live_failed.title": {"he": "הקלטת השידור החי נעצרה", "en": "Live recording stopped"},
    "live_failed.body": {"he": "„{project}”: {reason}", "en": "“{project}”: {reason}"},

    "publish_complete.title": {"he": "פורסם", "en": "Published"},
    "publish_complete.body": {
        "he": "„{title}” פורסם ב-{platform} ({account}).",
        "en": "“{title}” was published on {platform} ({account})."},

    "publish_scheduled.title": {"he": "תוזמן לפרסום", "en": "Scheduled"},
    "publish_scheduled.body": {
        "he": "„{title}” יפורסם ב-{platform} ({account}) ב-{when}.",
        "en": "“{title}” will be published on {platform} ({account}) at {when}."},

    "publish_failed.title": {"he": "הפרסום נכשל", "en": "Publishing failed"},
    "publish_failed.body": {
        "he": "„{title}” ב-{platform} ({account}):",
        "en": "“{title}” on {platform} ({account}):"},

    "schedule_failed.title": {"he": "פרסום מתוזמן נכשל", "en": "Scheduled post failed"},
    "schedule_failed.body": {
        "he": "„{title}” לא פורסם ב-{platform} ({account}) בזמן שנקבע:",
        "en": "“{title}” was not published on {platform} ({account}) at the scheduled time:"},

    "account_reconnect.title": {"he": "צריך לחבר מחדש חשבון", "en": "Reconnect an account"},
    "account_reconnect.body": {
        "he": "החיבור ל-{platform} ({account}) פג או בוטל. חברו אותו מחדש כדי להמשיך לפרסם.",
        "en": "The connection to {platform} ({account}) expired or was revoked. Reconnect it to keep publishing."},
}
