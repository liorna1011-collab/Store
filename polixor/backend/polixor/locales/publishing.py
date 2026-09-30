"""פרסום לרשתות (services/publishing)."""

NAMESPACE = "publishing"

MESSAGES = {
    # ---- סטטוס יעד ----
    "status.queued": {"he": "בתור", "en": "Queued"},
    "status.scheduled": {"he": "מתוזמן (Polixor יפרסם)", "en": "Scheduled (Polixor will publish)"},
    "status.uploading": {"he": "מעלה", "en": "Uploading"},
    "status.processing": {"he": "בעיבוד בפלטפורמה", "en": "Processing on the platform"},
    "status.scheduled_on_platform": {"he": "מתוזמן בפלטפורמה", "en": "Scheduled on the platform"},
    "status.published": {"he": "פורסם", "en": "Published"},
    "status.failed": {"he": "נכשל", "en": "Failed"},
    "status.cancelled": {"he": "בוטל", "en": "Cancelled"},

    "group.needs_attention": {"he": "דורש טיפול", "en": "Needs attention"},
    "group.in_progress": {"he": "בתהליך", "en": "In progress"},
    "group.scheduled": {"he": "מתוזמן", "en": "Scheduled"},
    "group.published": {"he": "פורסם", "en": "Published"},
    "group.cancelled": {"he": "בוטל", "en": "Cancelled"},

    "account_status.connected": {"he": "מחובר", "en": "Connected"},
    "account_status.reconnect_required": {"he": "צריך להתחבר מחדש", "en": "Reconnect needed"},
    "account_status.revoked": {"he": "ההרשאה בוטלה", "en": "Access revoked"},

    "format.short": {"he": "סרטון קצר (אנכי)", "en": "short vertical video"},
    "format.long": {"he": "סרטון ארוך", "en": "long video"},

    # ---- בעיות בבדיקה המוקדמת ----
    "issue.clip_missing": {"he": "הסרטון לא נמצא.", "en": "The video was not found."},
    "issue.clip_not_ready": {"he": "הסרטון עוד לא מוכן.", "en": "The video isn't ready yet."},
    "issue.file_missing": {"he": "קובץ הסרטון חסר. ייצאו אותו מחדש.",
                           "en": "The video file is missing. Export it again."},
    "issue.no_targets": {"he": "בחרו לפחות חשבון אחד.", "en": "Choose at least one account."},
    "issue.account_missing": {"he": "החשבון לא נמצא (אולי נותק).",
                              "en": "The account was not found (it may have been disconnected)."},
    "issue.duplicate_target": {"he": "החשבון נבחר פעמיים.", "en": "This account was selected twice."},
    "issue.platform_unavailable": {"he": "פרסום ל-{platform} עוד לא זמין.",
                                   "en": "Publishing to {platform} isn't available yet."},
    "issue.reconnect": {"he": "צריך לחבר מחדש את החשבון ב-{platform}.",
                        "en": "Reconnect this {platform} account first."},
    "issue.format_unsupported": {"he": "{platform} לא מקבל {format} דרך הפרסום האוטומטי.",
                                 "en": "{platform} doesn't accept a {format} through automatic publishing."},
    "issue.too_long": {"he": "הסרטון ארוך מהמותר ({seconds} שניות).",
                       "en": "The video is longer than allowed ({seconds} seconds)."},
    "issue.too_big": {"he": "הקובץ גדול מהמותר ({mb}MB).", "en": "The file is larger than allowed ({mb} MB)."},
    "issue.title_missing": {"he": "חסרה כותרת.", "en": "A title is required."},
    "issue.title_too_long": {"he": "הכותרת ארוכה מדי (עד {max} תווים).",
                             "en": "The title is too long (up to {max} characters)."},
    "issue.description_too_long": {"he": "התיאור ארוך מדי (עד {max} תווים).",
                                   "en": "The description is too long (up to {max} characters)."},
    "issue.too_many_tags": {"he": "יותר מדי תגיות (עד {max}).", "en": "Too many tags (up to {max})."},
    "issue.privacy_unsupported": {"he": "הגדרת הפרטיות „{privacy}” לא נתמכת כאן.",
                                  "en": "The privacy setting “{privacy}” isn't supported here."},
    "issue.schedule_missing": {"he": "בחרו מועד לפרסום.", "en": "Choose a time to publish."},
    "issue.schedule_past": {"he": "המועד שנבחר כבר עבר.", "en": "The chosen time has already passed."},
    "issue.schedule_too_far": {"he": "אפשר לתזמן עד {days} ימים קדימה.",
                               "en": "You can schedule up to {days} days ahead."},

    "issue.youtube_short_not_vertical": {
        "he": "קליפ קצר ל-YouTube צריך להיות אנכי או ריבועי (אחרת הוא לא יוצג כ-Short).",
        "en": "A short for YouTube must be vertical or square (otherwise it won't show as a Short)."},
    "issue.youtube_title_brackets": {"he": "YouTube לא מקבל את התווים < או > בכותרת.",
                                     "en": "YouTube doesn't accept < or > in the title."},
    "issue.youtube_tags_too_long": {"he": "התגיות ארוכות מדי יחד (עד {max} תווים).",
                                    "en": "The tags are too long together (up to {max} characters)."},

    "issue.meta_caption_too_long": {
        "he": "הכיתוב (כותרת + תיאור + תגיות) ארוך מדי – עד {max} תווים.",
        "en": "The caption (title + description + tags) is too long – up to {max} characters."},
    "issue.meta_too_many_hashtags": {"he": "יותר מדי האשטגים (עד {max}).",
                                     "en": "Too many hashtags (up to {max})."},
    "issue.meta_too_short": {"he": "הסרטון קצר מדי (לפחות {seconds} שניות).",
                             "en": "The video is too short (at least {seconds} seconds)."},
    "issue.meta_cover_out_of_range": {"he": "רגע הכריכה מחוץ לאורך הסרטון.",
                                      "en": "The cover moment is outside the video."},
    "issue.facebook_reel_not_vertical": {"he": "Reel ב-Facebook חייב להיות אנכי (9:16).",
                                         "en": "A Facebook Reel must be vertical (9:16)."},
    "issue.facebook_reel_resolution": {"he": "Reel ב-Facebook צריך לפחות {w}x{h}.",
                                       "en": "A Facebook Reel needs at least {w}x{h}."},
    "issue.facebook_schedule_too_soon": {
        "he": "ב-Facebook אפשר לתזמן רק לפחות {minutes} דקות קדימה.",
        "en": "Facebook only accepts a scheduled time at least {minutes} minutes ahead."},

    # ---- אזהרות (לא חוסמות) ----
    "warning.instagram_horizontal": {
        "he": "ב-Instagram הסרטון יתפרסם כ-Reel; סרטון אופקי יוצג עם פסים.",
        "en": "On Instagram the video is published as a Reel; a horizontal video shows with bars."},
    "warning.youtube_private_until_audit": {
        "he": "פרויקט ה-API של YouTube עוד לא עבר ביקורת של Google, ולכן YouTube יעלה את הסרטון כפרטי. אחרי שהפרויקט מאושר – סמנו זאת בהגדרות → פרסום.",
        "en": "Your YouTube API project hasn't passed Google's audit yet, so YouTube will upload the video as private. Once it's approved, mark it in Settings → Publishing."},
    "warning.youtube_long_needs_verified": {
        "he": "סרטון ארוך מ-{minutes} דקות עולה רק לערוץ מאומת ב-YouTube.",
        "en": "Videos longer than {minutes} minutes only upload to a verified YouTube channel."},

    # ---- שגיאות ----
    "error.csrf": {"he": "הבקשה נחסמה (לא נשלחה מ-Polixor).",
                   "en": "The request was blocked (it didn't come from Polixor)."},
    "error.platform_unavailable": {"he": "פרסום ל-{platform} עוד לא זמין.",
                                   "en": "Publishing to {platform} isn't available yet."},
    "error.not_configured": {
        "he": "חסרים פרטי אפליקציית המפתחים של {platform}. הוסיפו אותם בפרסום → חשבונות.",
        "en": "The {platform} developer app details are missing. Add them in Publishing → Accounts."},
    "error.oauth_state": {"he": "החיבור פג או כבר נוצל. נסו להתחבר שוב.",
                          "en": "The connection link expired or was already used. Try connecting again."},
    "error.oauth_denied": {"he": "החיבור בוטל בפלטפורמה.", "en": "The connection was cancelled on the platform."},
    "error.oauth_failed": {"he": "החיבור לחשבון נכשל. נסו שוב.",
                           "en": "Connecting the account failed. Try again."},
    "error.reconnect": {"he": "ההרשאה לחשבון פגה או בוטלה – צריך להתחבר מחדש.",
                        "en": "Access to the account expired or was revoked – reconnect it."},
    "error.account_removed": {"he": "החשבון נותק.", "en": "The account was disconnected."},
    "error.file_missing": {"he": "קובץ הסרטון חסר.", "en": "The video file is missing."},
    "error.temporary": {"he": "תקלה זמנית בפלטפורמה.", "en": "A temporary problem on the platform."},
    "error.rejected": {"he": "הפלטפורמה דחתה את הסרטון ({reason}).",
                       "en": "The platform rejected the video ({reason})."},
    "error.quota": {"he": "הגעתם למכסת הפרסום של הפלטפורמה. נסו מאוחר יותר.",
                    "en": "You reached the platform's posting limit. Try again later."},
    "error.interrupted": {
        "he": "ההעלאה ל-{platform} נקטעה (השרת נעצר באמצעה). בדקו בפלטפורמה אם הסרטון עלה לפני שמנסים שוב.",
        "en": "The upload to {platform} was interrupted (the server stopped). Check on the platform whether it went up before retrying."},
    "error.missed": {
        "he": "מועד הפרסום עבר כשהשרת היה כבוי (יותר מ-{minutes} דקות). אפשר לנסות שוב עכשיו.",
        "en": "The scheduled time passed while the server was off (more than {minutes} minutes). You can retry now."},
    "error.preflight": {"he": "יש בעיות שצריך לתקן לפני הפרסום.",
                        "en": "Some problems need fixing before publishing."},
    "error.scope_missing": {
        "he": "לא ניתנה ההרשאה להעלות סרטונים ל-{platform}. התחברו שוב ואשרו את כל ההרשאות.",
        "en": "Permission to upload to {platform} wasn't granted. Connect again and approve all permissions."},
    "error.youtube_no_channel": {"he": "לחשבון Google הזה אין ערוץ YouTube.",
                                 "en": "This Google account has no YouTube channel."},
    "error.meta_no_pages": {
        "he": "לא נמצא עמוד Facebook שיש לכם הרשאה לפרסם בו. בחרו עמוד בחלון ההרשאות של Facebook.",
        "en": "No Facebook Page you can post to was found. Select a Page in Facebook's permission window."},
    "error.meta_no_instagram": {
        "he": "לא נמצא חשבון Instagram מקצועי (Business/Creator) שמקושר לעמוד Facebook. קשרו אותו בהגדרות ה-Instagram ונסו שוב.",
        "en": "No Instagram professional (Business/Creator) account linked to a Facebook Page was found. Link it in Instagram settings and try again."},
    "error.processing_timeout": {
        "he": "{platform} עדיין מעבד את הסרטון אחרי זמן רב. בדקו בפלטפורמה לפני שמנסים שוב.",
        "en": "{platform} is still processing the video after a long time. Check on the platform before retrying."},
    "error.bad_mode": {"he": "מצב פרסום לא מוכר.", "en": "Unknown publishing mode."},
    "error.job_missing": {"he": "הפרסום לא נמצא.", "en": "The post was not found."},
    "error.cancel_on_platform": {
        "he": "הפרסום כבר מתוזמן ב-{platform}. בטלו אותו שם.",
        "en": "It's already scheduled on {platform}. Cancel it there."},
    "error.cannot_cancel": {"he": "אי אפשר לבטל בשלב הזה.", "en": "It can't be cancelled at this stage."},
    "error.cannot_retry": {"he": "אפשר לנסות שוב רק פרסום שנכשל.", "en": "Only a failed post can be retried."},
    "error.bad_credential": {"he": "הערך לא תקין.", "en": "The value isn't valid."},
    "error.timezone_required": {"he": "המועד חייב לכלול אזור זמן.", "en": "The time must include a time zone."},

    # ---- דף האישור של ארגז החול ----
    "sandbox.title": {"he": "ארגז חול – חיבור חשבון", "en": "Sandbox – connect account"},
    "sandbox.body": {
        "he": "זה חשבון בדיקה. שום דבר לא יפורסם באמת – „הפוסטים” נרשמים רק אצלכם, כדי לבדוק את הזרימה.",
        "en": "This is a test account. Nothing is really published – “posts” are only recorded locally so you can test the flow."},
    "sandbox.approve": {"he": "אישור", "en": "Approve"},
    "sandbox.deny": {"he": "ביטול", "en": "Cancel"},
}
