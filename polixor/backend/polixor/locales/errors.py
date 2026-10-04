"""הודעות שגיאה ורמזים לפעולה. מפתח: <code>.message / <code>.hint."""

NAMESPACE = "errors"

MESSAGES = {
    # ---- כללי ----
    "unknown_error.message": {
        "he": "אירעה שגיאה לא צפויה.",
        "en": "An unexpected error occurred."},
    "unexpected.message": {
        "he": "שגיאה לא צפויה בעיבוד.",
        "en": "Unexpected processing error."},
    "engine_not_ready.message": {
        "he": "מנוע העיבוד לא אותחל.",
        "en": "The processing engine is not initialised."},
    "cancelled.message": {
        "he": "המשימה בוטלה.",
        "en": "The task was cancelled."},
    "job_not_found.message": {
        "he": "המשימה לא נמצאה.",
        "en": "Task not found."},
    "clip_not_found.message": {
        "he": "הקליפ לא נמצא.",
        "en": "Clip not found."},
    "project_not_found.message": {
        "he": "הפרויקט לא נמצא.",
        "en": "Project not found."},
    "project_busy.message": {
        "he": "הפרויקט בעיבוד כרגע.",
        "en": "The project is being processed right now."},
    "project_busy.hint": {
        "he": "המתן לסיום העיבוד או בטל אותו לפני שינוי ההגדרות.",
        "en": "Wait for processing to finish, or cancel it, before changing settings."},
    "analysis_incomplete.message": {
        "he": "ניתוח התוכן עדיין לא הושלם.",
        "en": "Content analysis has not finished yet."},
    "analysis_incomplete.hint": {
        "he": "המתן לסיום הניתוח ואז נסה שוב.",
        "en": "Wait for the analysis to finish, then try again."},
    "interrupted.message": {
        "he": "השרת נסגר באמצע העיבוד.",
        "en": "The server was shut down during processing."},
    "interrupted.hint": {
        "he": "ניתן להמשיך מהשלב האחרון שהושלם.",
        "en": "You can resume from the last completed stage."},
    "already_running.message": {
        "he": "המשימה כבר רצה.",
        "en": "The task is already running."},
    "missing_input.message": {
        "he": "יש להזין קישור או להעלות קובץ.",
        "en": "Paste a link or upload a file."},
    "upload_missing.message": {
        "he": "הקובץ שהועלה לא נמצא. נסה להעלות שוב.",
        "en": "The uploaded file was not found. Please upload it again."},
    "unsupported_format.message": {
        "he": "סוג הקובץ {ext} אינו נתמך.",
        "en": "The file type {ext} is not supported."},
    "unsupported_format.hint": {
        "he": "פורמטים נתמכים: {formats}",
        "en": "Supported formats: {formats}"},
    "upload_not_found.message": {
        "he": "ההעלאה הזאת לא נמצאה (אולי פג תוקפה או בוטלה).",
        "en": "This upload was not found (it may have expired or been cancelled)."},
    "upload_not_found.hint": {
        "he": "בחרו את הקובץ שוב – ההעלאה תתחיל מחדש.",
        "en": "Choose the file again – the upload starts over."},
    "upload_no_space.message": {
        "he": "אין מספיק מקום פנוי בשרת לקובץ הזה: נדרשים {need}, פנויים {free}.",
        "en": "Not enough free space on the server for this file: {need} needed, {free} free."},
    "upload_no_space.hint": {
        "he": "פנו מקום (מחיקת פרויקטים ישנים או ניקוי קבצים זמניים) ונסו שוב.",
        "en": "Free some space (delete old projects or clean temporary files) and try again."},
    "upload_too_large.message": {
        "he": "הקובץ גדול מהמותר ({max}).",
        "en": "The file is larger than allowed ({max})."},
    "upload_closed.message": {
        "he": "ההעלאה הזאת כבר לא פעילה ({status}).",
        "en": "This upload is no longer active ({status})."},
    "upload_bad_chunk.message": {
        "he": "חלק {index} אינו חלק תקין של הקובץ.",
        "en": "Part {index} is not a valid part of the file."},
    "upload_bad_chunk_size.message": {
        "he": "חלק {index} הגיע חלקי – הוא יישלח שוב.",
        "en": "Part {index} arrived incomplete – it will be sent again."},
    "upload_checksum.message": {
        "he": "חלק {index} הגיע פגום (סכום ביקורת לא תואם) – הוא יישלח שוב.",
        "en": "Part {index} arrived damaged (checksum mismatch) – it will be sent again."},
    "upload_incomplete.message": {
        "he": "חסרים עדיין {count} חלקים מהקובץ (הראשון: {first}).",
        "en": "{count} parts of the file are still missing (first: {first})."},
    "upload_size_mismatch.message": {
        "he": "גודל הקובץ שהתקבל לא תואם לגודל המקורי.",
        "en": "The received file size does not match the original."},
    "upload_busy.message": {
        "he": "הקובץ נבדק כרגע – רגע אחד.",
        "en": "The file is being verified right now – one moment."},
    "upload_invalid_video.message": {
        "he": "הקובץ הועלה במלואו אבל אינו וידאו קריא: {reason}",
        "en": "The file arrived complete but is not a readable video: {reason}"},
    "upload_invalid_video.hint": {
        "he": "בדקו שהקובץ נפתח בנגן וידאו, או ייצאו אותו מחדש כ-MP4.",
        "en": "Check that the file plays in a video player, or export it again as MP4."},
    "empty_file.message": {
        "he": "הקובץ שהועלה ריק.",
        "en": "The uploaded file is empty."},
    "upload_write_failed.message": {
        "he": "כתיבת הקובץ נכשלה (ייתכן שאין מקום בדיסק).",
        "en": "Writing the file failed (the disk may be full)."},
    "file_not_found.message": {
        "he": "הקובץ לא נמצא.",
        "en": "File not found."},
    "source_missing.message": {
        "he": "קובץ המקור כבר אינו קיים.",
        "en": "The source file no longer exists."},
    "source_missing.hint": {
        "he": "ניתן להריץ את המשימה מחדש עם אותו קישור.",
        "en": "You can run the task again with the same link."},
    "no_source.message": {
        "he": "אין קובץ מקור למשימה.",
        "en": "The task has no source file."},
    "not_video_file.message": {
        "he": "הקובץ אינו מכיל וידאו.",
        "en": "The file does not contain video."},
    "bad_range.message": {
        "he": "טווח הזמן שנבחר קצר מדי.",
        "en": "The selected time range is too short."},
    "file_missing.message": {
        "he": "קובץ הווידאו אינו זמין.",
        "en": "The video file is not available."},
    "no_subtitles.message": {
        "he": "אין כתוביות לקליפ הזה.",
        "en": "This clip has no subtitles."},
    "no_files.message": {
        "he": "לא נמצאו קבצים להורדה.",
        "en": "No files were found to download."},
    "thumb_missing.message": {
        "he": "אין תמונה ממוזערת.",
        "en": "No thumbnail available."},
    "frame_failed.message": {
        "he": "לא ניתן לחלץ פריים מהמקור.",
        "en": "Could not extract a frame from the source."},
    "not_found.message": {
        "he": "נתיב לא קיים.",
        "en": "Route not found."},
    "preview_failed.message": {
        "he": "יצירת התצוגה המקדימה נכשלה.",
        "en": "Creating the preview failed."},
    "invalid_style.message": {
        "he": "הגדרות הכתוביות אינן תקינות.",
        "en": "The subtitle settings are not valid."},
    "preview_text_too_long.message": {
        "he": "טקסט התצוגה המקדימה ארוך מדי (עד {limit} תווים).",
        "en": "The preview text is too long (up to {limit} characters)."},
    "bad_aspect.message": {
        "he": "יחס התצוגה {aspect} אינו נתמך.",
        "en": "The aspect ratio {aspect} is not supported."},
    "bad_aspect.hint": {
        "he": "יחסים נתמכים: {allowed}.",
        "en": "Supported ratios: {allowed}."},
    "bad_frame_size.message": {
        "he": "גודל הפריים אינו תקין ({width}×{height}).",
        "en": "The frame size is not valid ({width}×{height})."},
    "bad_frame_size.hint": {
        "he": "יש לבחור יחס תצוגה (9:16, 1:1, 4:5, 16:9) או רוחב וגובה בין 240 ל-4096.",
        "en": "Choose an aspect ratio (9:16, 1:1, 4:5, 16:9) or a width and height between 240 and 4096."},

    # ---- קישורים ופלטפורמות ----
    "invalid_url.message": {
        "he": "הקישור שהוזן אינו תקין או אינו נתמך.",
        "en": "The link is invalid or not supported."},
    "invalid_url.hint": {
        "he": "נסה להדביק קישור מלא ל-YouTube, Twitch, Kick או Google Drive.",
        "en": "Paste a full YouTube, Twitch, Kick or Google Drive link."},
    "url_empty.message": {
        "he": "לא הוזן קישור.",
        "en": "No link was entered."},
    "url_too_long.message": {
        "he": "הקישור ארוך מדי.",
        "en": "The link is too long."},
    "url_bad_scheme.message": {
        "he": "רק קישורי http ו-https נתמכים.",
        "en": "Only http and https links are supported."},
    "url_credentials.message": {
        "he": "קישורים עם שם משתמש או סיסמה אינם נתמכים.",
        "en": "Links that contain a username or password are not supported."},
    "url_credentials.hint": {
        "he": "הסר את פרטי ההתחברות מהקישור. Polixor לא שולח פרטי התחברות לאתרים.",
        "en": "Remove the credentials from the link. Polixor never sends credentials to sites."},
    "gdrive_no_id.message": {
        "he": "לא זוהה מזהה קובץ בקישור של Google Drive.",
        "en": "No file ID was found in the Google Drive link."},
    "gdrive_no_id.hint": {
        "he": "השתמש בקישור מסוג https://drive.google.com/file/d/<ID>/view",
        "en": "Use a link like https://drive.google.com/file/d/<ID>/view"},
    "unsupported_platform.message": {
        "he": "הפלטפורמה הזו אינה נתמכת כרגע.",
        "en": "This platform is not supported right now."},
    "not_a_video.message": {
        "he": "הקישור אינו מצביע על סרטון או שידור.",
        "en": "The link does not point to a video or a stream."},
    "not_a_video.hint": {
        "he": "פתח את הסרטון עצמו בדפדפן והעתק את הקישור שלו.",
        "en": "Open the video itself in your browser and copy its link."},
    "listing_page.message": {
        "he": "זהו דף רשימה ({page}) ולא סרטון בודד.",
        "en": "This is a listing page ({page}), not a single video."},
    "playlist_not_supported.message": {
        "he": "זהו קישור לרשימת השמעה ולא לסרטון בודד.",
        "en": "This is a playlist link, not a single video."},
    "playlist_not_supported.hint": {
        "he": "פתח סרטון אחד מהרשימה והעתק את הקישור שלו.",
        "en": "Open one video from the playlist and copy its link."},
    "blocked_address.message": {
        "he": "הקישור מפנה לכתובת פנימית או מקומית, ולכן נחסם.",
        "en": "The link points to an internal or local address and was blocked."},
    "blocked_address.hint": {
        "he": "ניתן לייבא רק קישורים לאתרים ציבוריים. קובץ מהמחשב – השתמש בהעלאה.",
        "en": "Only public websites can be imported. For a file on your computer, use Upload."},
    "unresolvable_host.message": {
        "he": "לא ניתן לאתר את השרת של הקישור ({host}).",
        "en": "The link's server could not be found ({host})."},
    "live_requires_capture.message": {
        "he": "זהו שידור חי. יש לבחור כמה דקות להקליט ממנו.",
        "en": "This is a live stream. Choose how many minutes to record."},
    "live_requires_capture.hint": {
        "he": "שידור חי אינו מורד בלי הגבלה: ההקלטה מתחילה מעכשיו ונמשכת לפי הזמן שבחרת.",
        "en": "Live streams are never downloaded without a limit: recording starts now and lasts as long as you choose."},
    "source_too_long.message": {
        "he": "הסרטון ארוך מהמגבלה ({hours} שעות).",
        "en": "The video is longer than the limit ({hours} hours)."},
    "source_too_long.hint": {
        "he": "בחר טווח זמן (התחלה וסיום) לייבוא, או העלה את המגבלה בהגדרות.",
        "en": "Choose a time range (start and end) to import, or raise the limit in Settings."},
    "invalid_section.message": {
        "he": "טווח הזמן שנבחר אינו תקין.",
        "en": "The selected time range is not valid."},
    "invalid_section.hint": {
        "he": "זמן הסיום צריך להיות אחרי זמן ההתחלה ובתוך אורך הסרטון.",
        "en": "The end time must be after the start time and within the video."},
    "sign_in_required.message": {
        "he": "הפלטפורמה דורשת התחברות כדי לאשר שאינך רובוט.",
        "en": "The platform asks you to sign in to confirm you're not a bot."},
    "sign_in_required.hint": {
        "he": "Polixor אינו עוקף בדיקות כאלה. הורד את הסרטון בדרך מורשית והעלה את הקובץ, או נסה שוב מאוחר יותר.",
        "en": "Polixor does not bypass such checks. Download the video in an authorised way and upload the file, or try again later."},
    "members_only.message": {
        "he": "הסרטון זמין לחברי ערוץ בלבד.",
        "en": "This video is for channel members only."},
    "members_only.hint": {
        "he": "Polixor אינו עוקף הגבלות גישה. אם יש לך זכות לתוכן, העלה את הקובץ ידנית.",
        "en": "Polixor does not bypass access restrictions. If you have the right to the content, upload the file yourself."},
    "rate_limited.message": {
        "he": "הפלטפורמה הגבילה זמנית את מספר הבקשות.",
        "en": "The platform is temporarily limiting requests."},
    "rate_limited.hint": {
        "he": "המתן כמה דקות ונסה שוב.",
        "en": "Wait a few minutes and try again."},
    "extractor_failed.message": {
        "he": "לא ניתן היה לקרוא את פרטי הסרטון מהפלטפורמה.",
        "en": "Could not read the video details from the platform."},
    "extractor_failed.hint": {
        "he": "ייתכן שהאתר השתנה. עדכן את yt-dlp (pip install -U yt-dlp) או הורד את הסרטון ידנית והעלה אותו.",
        "en": "The site may have changed. Update yt-dlp (pip install -U yt-dlp), or download the video manually and upload it."},
    "download_failed.message": {
        "he": "ההורדה נכשלה.",
        "en": "The download failed."},
    "download_failed.hint": {
        "he": "בדוק את הקישור ואת החיבור לרשת.",
        "en": "Check the link and your network connection."},
    "downloaded_file_missing.message": {
        "he": "קובץ הווידאו לא נמצא לאחר ההורדה.",
        "en": "The video file was not found after downloading."},
    "downloaded_file_missing.hint": {
        "he": "נסה שוב, או הורד ידנית והעלה את הקובץ.",
        "en": "Try again, or download it manually and upload the file."},
    "ytdlp_missing.message": {
        "he": "yt-dlp אינו מותקן.",
        "en": "yt-dlp is not installed."},
    "ytdlp_missing.hint": {
        "he": "הרץ: pip install -r requirements.txt",
        "en": "Run: pip install -r requirements.txt"},
    "private_or_unavailable.message": {
        "he": "הסרטון פרטי, הוסר, או אינו זמין להורדה.",
        "en": "The video is private, removed, or not available for download."},
    "private_or_unavailable.hint": {
        "he": "ודא שהסרטון ציבורי. Polixor לא עוקף הגבלות גישה או הרשאות.",
        "en": "Make sure the video is public. Polixor does not bypass access restrictions or permissions."},
    "drm_protected.message": {
        "he": "התוכן מוגן ב-DRM ולכן לא ניתן לעבד אותו.",
        "en": "The content is DRM-protected, so it cannot be processed."},
    "drm_protected.hint": {
        "he": "Polixor לא עוקף מנגנוני הגנה. יש להשתמש במקור שיש לך זכות להוריד.",
        "en": "Polixor does not bypass protection mechanisms. Use a source you have the right to download."},
    "restricted.message": {
        "he": "הגישה לסרטון מוגבלת (אזור גאוגרפי, גיל או התחברות).",
        "en": "Access to the video is restricted (region, age or sign-in)."},
    "restricted.hint": {
        "he": "ניתן להוריד את הקובץ ידנית ולהעלות אותו לתוכנה.",
        "en": "You can download the file manually and upload it."},
    "network_error.message": {
        "he": "לא ניתן להגיע לשרת של הפלטפורמה.",
        "en": "The platform's server could not be reached."},
    "network_error.hint": {
        "he": "בדוק את החיבור לאינטרנט. אם אתה מאחורי חומת אש או פרוקסי, ייתכן שהגישה לפלטפורמה חסומה מהמחשב הזה.",
        "en": "Check your internet connection. Behind a firewall or proxy, the platform may be blocked from this computer."},

    # ---- שידור חי ----
    "live_not_started.message": {
        "he": "השידור החי טרם התחיל או שאינו זמין כרגע.",
        "en": "The live stream has not started or is not available right now."},
    "live_unavailable.message": {
        "he": "השידור אינו זמין כרגע.",
        "en": "The stream is not available right now."},
    "live_unavailable.hint": {
        "he": "ודא שהשידור באוויר ושהקישור ציבורי.",
        "en": "Make sure the stream is on air and the link is public."},
    "live_nothing_captured.message": {
        "he": "לא נאסף חומר מההקלטה.",
        "en": "Nothing was captured from the recording."},
    "live_nothing_captured.hint": {
        "he": "ייתכן שהשידור הסתיים לפני שהצטבר חומר.",
        "en": "The stream may have ended before any material was collected."},
    "live_invalid_state.message": {
        "he": "הפעולה אינה אפשרית במצב הנוכחי של ההקלטה.",
        "en": "This action is not possible in the recording's current state."},
    "live_already_running.message": {
        "he": "ההקלטה כבר פעילה.",
        "en": "The recording is already running."},
    "live_not_active.message": {
        "he": "אין הקלטה פעילה לעצור.",
        "en": "There is no active recording to stop."},

    # ---- מדיה ועיבוד ----
    "no_audio.message": {
        "he": "לא נמצא ערוץ אודיו בסרטון.",
        "en": "No audio track was found in the video."},
    "no_audio.hint": {
        "he": "ניתן עדיין לנתח ויזואלית: הפעל 'ניתוח חזותי בלבד' בהגדרות.",
        "en": "Visual analysis still works: enable 'visual analysis only' in Settings."},
    "ffmpeg_missing.message": {
        "he": "FFmpeg לא נמצא במערכת.",
        "en": "FFmpeg was not found on this system."},
    "ffmpeg_missing.hint": {
        "he": "התקן FFmpeg והוסף אותו ל-PATH, או הרץ scripts/install_windows.ps1.",
        "en": "Install FFmpeg and add it to PATH, or run scripts/install_windows.ps1."},
    "ffmpeg_failed.message": {
        "he": "פעולת FFmpeg נכשלה.",
        "en": "An FFmpeg operation failed."},
    "video_processing_failed.message": {
        "he": "עיבוד הווידאו נכשל.",
        "en": "Video processing failed."},
    "output_not_created.message": {
        "he": "קובץ הפלט לא נוצר כראוי.",
        "en": "The output file was not created correctly."},
    "corrupt_media.message": {
        "he": "לא ניתן לקרוא את קובץ הווידאו (ייתכן שהוא פגום).",
        "en": "The video file could not be read (it may be corrupt)."},
    "disk_space.message": {
        "he": "אין מספיק מקום פנוי בדיסק.",
        "en": "There is not enough free disk space."},
    "disk_space.hint": {
        "he": "פנה מקום או בחר תיקיית ייצוא בכונן אחר.",
        "en": "Free up space or choose an export folder on another drive."},
    "disk_space_needed.message": {
        "he": "אין מספיק מקום בדיסק: נדרשים כ-{needed}, פנויים {free}.",
        "en": "Not enough disk space: about {needed} is needed, {free} is free."},
    "source_too_short.message": {
        "he": "הסרטון קצר מדי לניתוח.",
        "en": "The video is too short to analyse."},
    "source_too_short.hint": {
        "he": "נדרש סרטון באורך של לפחות מספר שניות.",
        "en": "The video must be at least a few seconds long."},
    "source_too_short_seconds.message": {
        "he": "אורך הווידאו {seconds} שניות – קצר מדי לניתוח.",
        "en": "The video is {seconds} seconds long – too short to analyse."},
    "no_moments.message": {
        "he": "לא נמצאו רגעים מתאימים ליצירת קליפים.",
        "en": "No suitable moments were found for clips."},
    "no_moments.hint": {
        "he": "נסה להעלות את רמת הרגישות בהגדרות או להאריך את טווח אורך הקליפ.",
        "en": "Try raising the sensitivity in Settings or widening the clip length range."},
    "no_segments.message": {
        "he": "לא הוגדרו מקטעים לייצוא.",
        "en": "No segments were defined for export."},

    # ---- תמלול ו-AI ----
    "transcription_failed.message": {
        "he": "התמלול נכשל.",
        "en": "Transcription failed."},
    "transcription_failed.hint": {
        "he": "בדוק שהמודל הורד בהצלחה ושיש מספיק זיכרון פנוי.",
        "en": "Check that the model downloaded successfully and that there is enough free memory."},
    "model_unavailable.message": {
        "he": "מודל התמלול אינו זמין.",
        "en": "The transcription model is not available."},
    "model_unavailable.hint": {
        "he": "נדרשת גישה לאינטרנט להורדת המודל בפעם הראשונה.",
        "en": "Internet access is needed to download the model the first time."},
    "ai_provider_failed.message": {
        "he": "הפנייה למודל ה-AI בענן נכשלה.",
        "en": "The request to the cloud AI model failed."},
    "ai_provider_failed.hint": {
        "he": "בדוק את מפתח ה-API בהגדרות. ניתן להמשיך במצב AI מקומי.",
        "en": "Check the API key in Settings. You can continue in local AI mode."},

    # ---- תמונות ----
    "image_failed.message": {
        "he": "יצירת התמונה נכשלה.",
        "en": "Image generation failed."},
    "image_key_missing.message": {
        "he": "לא הוגדר מפתח API ליצירת תמונות.",
        "en": "No API key is set for image generation."},
    "image_key_missing.hint": {
        "he": "הוסף מפתח OpenAI במסך ההגדרות → AI Images. המפתח נשמר מוצפן במחשב שלך ואינו נחשף בדפדפן.",
        "en": "Add an OpenAI key in Settings → AI Images. The key is stored encrypted on your computer and is never exposed to the browser."},
    "image_bad_prompt.message": {
        "he": "הפרומפט אינו תקין.",
        "en": "The prompt is not valid."},
    "image_rate_limit.message": {
        "he": "חרגת ממכסת הבקשות של ספק התמונות.",
        "en": "You exceeded the image provider's request quota."},
    "image_rate_limit.hint": {
        "he": "המתן דקה ונסה שוב, או בדוק את מצב החשבון שלך אצל הספק.",
        "en": "Wait a minute and try again, or check your account with the provider."},
    "image_timeout.message": {
        "he": "יצירת התמונה לקחה יותר מדי זמן.",
        "en": "Image generation took too long."},
    "image_timeout.hint": {
        "he": "נסה שוב, או קצר את הפרומפט.",
        "en": "Try again, or shorten the prompt."},
    "image_rejected.message": {
        "he": "ספק התמונות דחה את הבקשה.",
        "en": "The image provider rejected the request."},
    "image_rejected.hint": {
        "he": "ייתכן שהפרומפט חורג ממדיניות התוכן של הספק. נסח אותו אחרת.",
        "en": "The prompt may violate the provider's content policy. Rephrase it."},
    "image_bad_response.message": {
        "he": "התקבלה תשובה לא צפויה מספק התמונות.",
        "en": "The image provider returned an unexpected response."},
    "image_cancelled.message": {
        "he": "יצירת התמונה בוטלה.",
        "en": "Image generation was cancelled."},
    "image_unavailable.message": {
        "he": "ספק התמונות אינו זמין כרגע.",
        "en": "The image provider is not available right now."},
    "image_not_found.message": {
        "he": "התמונה לא נמצאה.",
        "en": "Image not found."},
    "image_not_ready.message": {
        "he": "התמונה עדיין לא מוכנה לשימוש.",
        "en": "The image is not ready yet."},
    "image_not_ready.hint": {
        "he": "המתן לסיום היצירה, או צור אותה מחדש אם היא נכשלה.",
        "en": "Wait for generation to finish, or create it again if it failed."},
    "invalid_role.message": {
        "he": "תפקיד תמונה לא מוכר.",
        "en": "Unknown image role."},
    "invalid_placement.message": {
        "he": "לא ניתן לשבץ את התמונה בנקודה הזו.",
        "en": "The image cannot be placed at this point."},

    # ---- נתיבי API: הגדרות, תמונות ושידור חי ----
    "image_file_missing.message": {
        "he": "קובץ התמונה חסר.",
        "en": "The image file is missing."},
    "image_file_missing.hint": {
        "he": "נסה ליצור את התמונה מחדש.",
        "en": "Try creating the image again."},
    "image_file_missing_disk.message": {
        "he": "קובץ התמונה חסר על הדיסק.",
        "en": "The image file is missing from the disk."},
    "no_variation_source.message": {
        "he": "אין קובץ מקור ליצירת וריאציה.",
        "en": "There is no source file to create a variation from."},
    "placement_not_found.message": {
        "he": "השיבוץ לא נמצא.",
        "en": "The placement was not found."},
    "unknown_secret.message": {
        "he": "שם סוד לא מוכר: {name}",
        "en": "Unknown secret name: {name}"},
    "unknown_secret.hint": {
        "he": "מותר: {allowed}",
        "en": "Allowed: {allowed}"},
    "cookiefile_missing.message": {
        "he": "קובץ העוגיות שצוין לא נמצא.",
        "en": "The specified cookies file was not found."},
    "cookiefile_missing.hint": {
        "he": "יש לספק נתיב מלא לקובץ cookies.txt שייצאת בעצמך.",
        "en": "Provide the full path to a cookies.txt file you exported yourself."},
    "bad_region.message": {
        "he": "אזור המצלמה חייב לכלול x, y, w, h בין 0 ל-1.",
        "en": "The camera region must include x, y, w, h between 0 and 1."},
    "bad_region_value.message": {
        "he": "הערך {key}={value} חורג מהטווח 0..1.",
        "en": "The value {key}={value} is outside the range 0..1."},
    "not_live.message": {
        "he": "המשימה אינה במצב שידור חי.",
        "en": "The job is not in live-stream mode."},
}
