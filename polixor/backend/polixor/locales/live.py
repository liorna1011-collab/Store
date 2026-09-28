"""קליטת שידור חי: זיהוי זרם, מצב הקלטה ותרגום תקלות של FFmpeg."""

NAMESPACE = "live"

MESSAGES = {
    # ---- זיהוי ----
    "detect.no_ytdlp": {"he": "yt-dlp אינו מותקן.", "en": "yt-dlp is not installed."},
    "detect.no_ytdlp_hint": {"he": "הרץ: pip install -r requirements.txt", "en": "Run: pip install -r requirements.txt"},
    "detect.no_info": {"he": "לא התקבל מידע על הקישור.", "en": "No information was received for the link."},
    "detect.not_stream": {"he": "הקישור אינו מצביע על שידור.", "en": "The link does not point to a stream."},
    "detect.upcoming": {"he": "השידור טרם התחיל.", "en": "The stream has not started yet."},
    "detect.not_live": {"he": "הקישור אינו שידור חי פעיל.", "en": "The link is not an active live stream."},
    "detect.no_stream": {"he": "לא נמצא זרם שניתן לקרוא ממנו.", "en": "No readable stream was found."},
    "detect.no_ffprobe": {"he": "ffprobe אינו זמין – פרטי הזרם לפי המטא-דאטה בלבד.",
                          "en": "ffprobe is not available – stream details come from metadata only."},
    "detect.probe_incomplete": {"he": "בדיקת הזרם לא הושלמה: {error}", "en": "Checking the stream did not finish: {error}"},
    "detect.probe_failed": {"he": "לא ניתן היה לקרוא מהזרם לצורך אימות.", "en": "The stream could not be read for verification."},
    "detect.inactive": {"he": "השידור אינו פעיל כרגע.", "en": "The stream is not active right now."},

    # ---- הקלטה ----
    "record.no_ffmpeg": {"he": "FFmpeg אינו זמין להקלטת שידור.", "en": "FFmpeg is not available to record the stream."},
    "record.killed": {"he": "FFmpeg לא הגיב ונסגר בכפייה.", "en": "FFmpeg did not respond and was force-closed."},
    "record.dropped": {"he": "החיבור לזרם נפל.", "en": "The connection to the stream dropped."},
    "record.interrupted": {"he": "ההקלטה נקטעה: {detail}", "en": "The recording was interrupted: {detail}"},
    "record.nothing": {"he": "לא נאסף חומר שניתן לעבד מההקלטה.", "en": "No usable material was collected from the recording."},
    "record.nothing_hint": {"he": "ייתכן שהשידור הסתיים לפני שהצטבר חומר.", "en": "The stream may have ended before material accumulated."},
    "record.no_ffmpeg_merge": {"he": "FFmpeg אינו זמין לאיחוד ההקלטה.", "en": "FFmpeg is not available to merge the recording."},
    "record.merge_failed": {"he": "איחוד מקטעי ההקלטה נכשל.", "en": "Merging the recording segments failed."},

    # ---- תקלות FFmpeg נפוצות ----
    "ffmpeg.404": {"he": "הזרם אינו זמין עוד (404). ייתכן שהשידור הסתיים.", "en": "The stream is no longer available (404). The stream may have ended."},
    "ffmpeg.403": {"he": "הגישה לזרם נדחתה (403). ייתכן שכתובת הזרם פגה.", "en": "Access to the stream was denied (403). The stream address may have expired."},
    "ffmpeg.refused": {"he": "החיבור לשרת השידור נדחה.", "en": "The connection to the streaming server was refused."},
    "ffmpeg.reset": {"he": "החיבור לשרת השידור נותק.", "en": "The connection to the streaming server was reset."},
    "ffmpeg.conn_timeout": {"he": "פג הזמן בהמתנה לשרת השידור.", "en": "Timed out waiting for the streaming server."},
    "ffmpeg.op_timeout": {"he": "פג הזמן בהמתנה לנתונים מהזרם.", "en": "Timed out waiting for data from the stream."},
    "ffmpeg.dns": {"he": "לא ניתן לאתר את שרת השידור.", "en": "The streaming server cannot be found."},
    "ffmpeg.5xx": {"he": "שרת השידור החזיר שגיאה זמנית.", "en": "The streaming server returned a temporary error."},
    "ffmpeg.bad_data": {"he": "התקבלו נתונים פגומים מהזרם.", "en": "Corrupt data was received from the stream."},
    "ffmpeg.disk_full": {"he": "נגמר המקום בדיסק בזמן ההקלטה.", "en": "The disk ran out of space during recording."},
    "ffmpeg.eof": {"he": "הזרם נגמר.", "en": "The stream ended."},

    # ---- מצב ההקלטה (live_capture) ----
    "capture.connecting": {"he": "מתחבר לזרם…", "en": "Connecting to the stream…"},
    "capture.recording": {"he": "מקליט", "en": "Recording"},
    "capture.failed_repeatedly": {"he": "החיבור נכשל {count} פעמים ברצף. ", "en": "The connection failed {count} times in a row. "},
    "capture.stopping": {"he": "מסיים הקלטה…", "en": "Finishing the recording…"},
    "capture.completed": {"he": "ההקלטה הושלמה", "en": "Recording complete"},
    "capture.nothing": {"he": "לא נאסף חומר מההקלטה.", "en": "No material was collected from the recording."},
    "capture.retrying": {"he": " מנסה להתחבר מחדש בעוד {seconds} שניות (ניסיון {attempt} מתוך {attempts})",
                         "en": " Reconnecting in {seconds} seconds (attempt {attempt} of {attempts})"},
    "capture.collected": {"he": "נאספו {minutes} דקות ב-{segments} מקטעים", "en": "Collected {minutes} minutes in {segments} segments"},
    "capture.reconnects": {"he": "{count} חיבורים מחדש", "en": "{count} reconnections"},
    "capture.stopped_by_user": {"he": "ההקלטה נעצרה על-ידי המשתמש", "en": "The recording was stopped by the user"},
}
