"""הודעות התקדמות, שלבים והערות של הפייפליין."""

NAMESPACE = "pipeline"

MESSAGES = {
    # ---- שמות שלבים ----
    "stage.pending": {"he": "ממתין", "en": "Waiting"},
    "stage.capture": {"he": "קליטת שידור", "en": "Recording stream"},
    "stage.download": {"he": "הורדה", "en": "Downloading"},
    "stage.probe": {"he": "בדיקת קובץ", "en": "Checking file"},
    "stage.audio": {"he": "חילוץ אודיו", "en": "Extracting audio"},
    "stage.transcribe": {"he": "תמלול", "en": "Transcribing"},
    "stage.analyze": {"he": "ניתוח", "en": "Analysing"},
    "stage.select": {"he": "בחירת רגעים", "en": "Choosing moments"},
    "stage.render_long": {"he": "יצירת וידאו ארוך", "en": "Creating long video"},
    "stage.render_short": {"he": "יצירת שורטים", "en": "Creating shorts"},
    "stage.done": {"he": "הושלם", "en": "Done"},

    # ---- שלבי פרויקט ----
    "phase.importing": {"he": "ייבוא", "en": "Importing"},
    "phase.analyzing": {"he": "ניתוח", "en": "Analysing"},
    "phase.configure": {"he": "הגדרות", "en": "Settings"},
    "phase.generating": {"he": "יצירה", "en": "Generating"},
    "phase.done": {"he": "מוכן", "en": "Ready"},
    "phase.failed": {"he": "נכשל", "en": "Failed"},
    "phase.cancelled": {"he": "בוטל", "en": "Cancelled"},

    # ---- סטטוס ----
    "status.queued": {"he": "ממתין בתור", "en": "Waiting in queue"},
    "status.starting": {"he": "מתחיל עיבוד", "en": "Starting"},
    "status.completed": {"he": "המשימה הושלמה", "en": "Finished"},
    "status.analysis_done": {"he": "הניתוח הושלם – אפשר לבחור מצב והגדרות", "en": "Analysis finished – choose a mode and settings"},
    "status.generate_done": {"he": "הקליפים מוכנים", "en": "Your clips are ready"},
    "status.cancelled": {"he": "המשימה בוטלה", "en": "Cancelled"},
    "status.cancelling": {"he": "מבטל את המשימה…", "en": "Cancelling…"},
    "status.resuming": {"he": "השרת הופעל מחדש – ממשיך מהשלב האחרון שהושלם (שום דבר לא מחושב מחדש).",
                        "en": "The server restarted – continuing from the last completed step (nothing is redone)."},
    "status.interrupted": {"he": "נקטע – ניתן לחדש מהשלב האחרון שהושלם.", "en": "Interrupted – you can resume from the last completed stage."},

    # ---- הקלטת שידור חי ----
    "capture.detecting": {"he": "מזהה את השידור…", "en": "Detecting the stream…"},
    "capture.detecting_short": {"he": "מזהה שידור…", "en": "Detecting stream…"},
    "capture.detected": {"he": "שידור זוהה: {platform}{title}{resolution}", "en": "Stream found: {platform}{title}{resolution}"},
    "capture.no_audio": {"he": "לא זוהה ערוץ אודיו בזרם. הניתוח יתבסס על הווידאו בלבד.", "en": "No audio track in the stream. Analysis will use video only."},
    "capture.segment_saved": {"he": "נשמר מקטע {n} · {duration}", "en": "Saved segment {n} · {duration}"},
    "capture.recording": {"he": "מקליט · {duration}", "en": "Recording · {duration}"},
    "capture.nothing": {"he": "לא נאסף חומר.", "en": "Nothing was captured."},
    "capture.merging": {"he": "מאחד את ההקלטה לקובץ אחד…", "en": "Joining the recording into one file…"},
    "capture.merge_failed": {"he": "איחוד ההקלטה נכשל. {n} מקטעים נשמרו בתיקייה {folder}", "en": "Joining the recording failed. {n} segments were kept in {folder}"},
    "capture.title_fallback": {"he": "שידור חי · {platform}", "en": "Live stream · {platform}"},
    "capture.reconnects": {"he": "הזרם נפל {n} פעמים במהלך ההקלטה; החומר שהוקלט לפני כל נפילה נשמר.", "en": "The stream dropped {n} times while recording; everything recorded before each drop was kept."},

    # ---- מצבי הקלטה ----
    "live_state.idle": {"he": "ממתין", "en": "Waiting"},
    "live_state.detecting": {"he": "מזהה שידור…", "en": "Detecting stream…"},
    "live_state.connecting": {"he": "מתחבר…", "en": "Connecting…"},
    "live_state.live": {"he": "משדר", "en": "Live"},
    "live_state.reconnecting": {"he": "מתחבר מחדש…", "en": "Reconnecting…"},
    "live_state.stopping": {"he": "עוצר הקלטה…", "en": "Stopping recording…"},
    "live_state.completed": {"he": "ההקלטה הושלמה", "en": "Recording finished"},
    "live_state.failed": {"he": "נכשל", "en": "Failed"},

    # ---- הורדה ----
    "download.start": {"he": "מוריד את הווידאו…", "en": "Downloading the video…"},
    "download.done": {"he": "הורד: {title} ({duration})", "en": "Downloaded: {title} ({duration})"},
    "download.progress": {"he": "{got}{total}{speed}{eta}", "en": "{got}{total}{speed}{eta}"},
    "download.of_total": {"he": " מתוך {total}", "en": " of {total}"},
    "download.speed": {"he": " · {speed}/ש'", "en": " · {speed}/s"},
    "download.eta": {"he": " · נותרו ~{seconds} שנ'", "en": " · ~{seconds}s left"},
    "download.merging": {"he": "ההורדה הושלמה, ממזג זרמים…", "en": "Download finished, merging streams…"},

    # ---- בדיקת קובץ ----
    "probe.start": {"he": "בודק את קובץ הווידאו…", "en": "Checking the video file…"},
    "probe.no_audio": {"he": "לא נמצא ערוץ אודיו: הניתוח יתבסס על וידאו בלבד, ולא ייווצרו תמלול או כתוביות.", "en": "No audio track: analysis will use video only, and no transcript or subtitles will be created."},

    # ---- אודיו ----
    "audio.start": {"he": "מחלץ אודיו…", "en": "Extracting audio…"},
    "audio.failed": {"he": "חילוץ האודיו נכשל ({message}). ממשיך עם וידאו בלבד.", "en": "Audio extraction failed ({message}). Continuing with video only."},

    # ---- תמלול ----
    "transcribe.start": {"he": "מתמלל את הדיבור…", "en": "Transcribing speech…"},
    "transcribe.no_audio": {"he": "אין אודיו לתמלול.", "en": "There is no audio to transcribe."},
    "transcribe.summary": {"he": "תמלול: {segments} מקטעים, שפה: {language}", "en": "Transcript: {segments} segments, language: {language}"},
    "transcribe.language_unknown": {"he": "לא זוהתה", "en": "not detected"},
    "transcribe.disabled": {"he": "תמלול מושבת – מנתח אודיו ווידאו בלבד", "en": "Transcription disabled – analysing audio and video only"},
    "transcribe.disabled_note": {"he": "התמלול הושבת בהגדרות. בחירת הרגעים מתבססת על אותות אודיו וּוידאו בלבד, ולא ייווצרו כתוביות.", "en": "Transcription is turned off in Settings. Moments are chosen from audio and video signals only, and no subtitles will be created."},
    "transcribe.fixture_loaded": {"he": "נטען תמלול בדיקה", "en": "Loaded test transcript"},
    "transcribe.fixture_note": {"he": "תמלול נטען מקובץ בדיקה (fixture) ולא הופק על-ידי מודל זיהוי דיבור.", "en": "The transcript was loaded from a test file (fixture), not produced by a speech-recognition model."},
    "transcribe.progress": {"he": "תומלל עד {time}", "en": "Transcribed up to {time}"},
    "transcribe.finished": {"he": "תמלול הושלם: {n} מקטעים", "en": "Transcription finished: {n} segments"},
    "transcribe.fallback_note": {"he": "{message} {hint} המשימה המשיכה ללא תמלול: הרגעים נבחרו לפי אודיו ווידאו, ולא נוצרו כתוביות.", "en": "{message} {hint} The task continued without a transcript: moments were chosen from audio and video, and no subtitles were created."},

    # ---- ניתוח ----
    "analyze.start": {"he": "מנתח אודיו ווידאו…", "en": "Analysing audio and video…"},
    "analyze.audio": {"he": "מנתח את פס הקול…", "en": "Analysing the soundtrack…"},
    "analyze.video": {"he": "מנתח את הווידאו…", "en": "Analysing the video…"},
    "analyze.frames": {"he": "מנתח פריימים…", "en": "Analysing frames…"},
    "analyze.layouts": {"he": "מזהה פריסת מסך ומצלמה…", "en": "Detecting screen and camera layout…"},
    "transcribe.language_uncertain": {
        "he": "זיהוי השפה לא בטוח ({language}, {pct}%). אם השפה שגויה, בחרו אותה במפורש בפרויקט ונתחו מחדש.",
        "en": "Language detection is uncertain ({language}, {pct}%). If it is wrong, choose the language explicitly in the project and analyse again."},
    "analyze.fusing": {"he": "משלב אותות…", "en": "Combining signals…"},
    "analyze.visual_windows": {
        "he": "מקור ארוך ({minutes} דקות) במצב מהיר: הניתוח החזותי המלא (פנים, פריסה, פעילות) ירוץ רק על הרגעים שייבחרו, ולא על כל השידור.",
        "en": "Long source ({minutes} min) in fast mode: full visual analysis (faces, layout, activity) runs only on the chosen moments, not on the whole stream."},
    "select.visual_windows": {"he": "ניתוח חזותי של {n} רגעים מועמדים…",
                              "en": "Visual analysis of {n} candidate moments…"},

    # ---- בחירה ----
    "select.start": {"he": "בוחר את הרגעים המעניינים…", "en": "Choosing the interesting moments…"},
    "select.shorts_found": {"he": "נמצאו {n} מועמדים לשורטים", "en": "Found {n} short-clip candidates"},
    "select.longs_found": {"he": "נמצאו {n} מועמדים לקליפים ארוכים", "en": "Found {n} long-clip candidates"},
    "select.no_highlights": {"he": "לא נמצאו מספיק רגעים לסרטון Highlights; נוצר קליפ ארוך רציף במקום.", "en": "Not enough moments for a Highlights video; a continuous long clip was created instead."},
    "select.llm_titles": {"he": "משפר כותרות עם מודל שפה…", "en": "Improving titles with a language model…"},
    "select.llm_discover": {"he": "מחפש רגעים שקטים שהאותות פספסו…", "en": "Looking for quiet moments the signals missed…"},
    "select.heuristic_note": {"he": "מצב AI מקומי (היוריסטי): הכותרות והתיאורים נגזרים מהתמלול ומהאותות, ללא מודל שפה. איכות הניסוח נמוכה יותר ממצב ענן.", "en": "Local (heuristic) AI mode: titles and descriptions come from the transcript and signals, without a language model. Wording quality is lower than cloud mode."},
    "select.chosen": {"he": "נבחרו {n} קטעים", "en": "Chose {n} segments"},
    "select.llm_title_fallback": {"he": "רגע מהשידור", "en": "Moment from the stream"},
    "select.llm_reason": {"he": "אותר על-ידי מודל שפה מתוך התמלול.", "en": "Found by a language model in the transcript."},

    # ---- יצירה ----
    "generate.clearing": {"he": "מכין את היצירה…", "en": "Preparing generation…"},
    "render.longs": {"he": "יוצר {n} קליפים ארוכים…", "en": "Creating {n} long clips…"},
    "render.shorts": {"he": "יוצר {n} שורטים…", "en": "Creating {n} shorts…"},
    "render.clip_progress": {"he": "יוצר קליפ {i} מתוך {n}", "en": "Creating clip {i} of {n}"},
    "render.clip_failed": {"he": "יצירת קליפ {i} נכשלה: {message}", "en": "Creating clip {i} failed: {message}"},
    "render.needs_review": {"he": "„{title}”: הקליפ נוצר אבל דורש בדיקה — {reasons}", "en": "“{title}”: the clip was created but needs review — {reasons}"},
    "render.cancelled": {"he": "בוטל", "en": "Cancelled"},
    "music.missing": {"he": "קובץ המוזיקה לא נמצא.", "en": "The music file was not found."},

    # ---- וידאו ארוך ----
    "longform.planning": {"he": "מתכנן את הסרטון הארוך…", "en": "Planning the long video…"},
    "longform.rendering": {"he": "יוצר את הסרטון הארוך ({parts} חלקים)…", "en": "Creating the long video ({parts} parts)…"},
    "longform.part": {"he": "יוצר חלק {i} מתוך {n}", "en": "Creating part {i} of {n}"},
}
