"""הודעות של שירותי העיבוד הבסיסיים: מודל שפה, תמלול, FFmpeg, דיסק, אודיו, וידאו וציר זמן."""

NAMESPACE = "processing"

MESSAGES = {
    # ---- מודל שפה ----
    "llm.no_anthropic_key": {"he": "לא הוגדר מפתח API של Anthropic.", "en": "No Anthropic API key is configured."},
    "llm.no_openai_key": {"he": "לא הוגדר מפתח API של OpenAI.", "en": "No OpenAI API key is configured."},
    "llm.no_key_hint": {"he": "הוסף מפתח במסך ההגדרות, או עבור למצב AI מקומי.", "en": "Add a key in Settings, or switch to a local AI mode."},
    "llm.provider_error": {"he": "{provider} החזיר שגיאה {code}.", "en": "{provider} returned error {code}."},
    "llm.ollama_unreachable": {"he": "לא ניתן להתחבר לשרת Ollama המקומי.", "en": "Cannot connect to the local Ollama server."},
    "llm.ollama_unreachable_hint": {
        "he": "ודא ש-Ollama פועל ({url}) ושהמודל '{model}' הורד באמצעות: ollama pull {model}",
        "en": "Make sure Ollama is running ({url}) and the model '{model}' was pulled with: ollama pull {model}"},
    "llm.no_model_mode": {"he": "מצב ה-AI הנוכחי אינו משתמש במודל שפה.", "en": "The current AI mode does not use a language model."},
    "llm.heuristic_note": {"he": "מצב היוריסטי מקומי – פעיל תמיד, ללא מודל שפה.", "en": "Local heuristic mode – always active, no language model."},
    "llm.ollama_running": {"he": "Ollama פועל מקומית.", "en": "Ollama is running locally."},
    "llm.ollama_not_responding": {"he": "Ollama לא מגיב.", "en": "Ollama is not responding."},
    "llm.ollama_connect_failed": {"he": "לא ניתן להתחבר ל-Ollama: {error}", "en": "Cannot connect to Ollama: {error}"},
    "llm.key_set": {"he": "מפתח API מוגדר.", "en": "An API key is configured."},
    "llm.key_missing": {"he": "חסר מפתח API בהגדרות.", "en": "No API key in settings."},
    "llm.empty_answer": {"he": "המודל החזיר תשובה ריקה.", "en": "The model returned an empty answer."},
    "llm.bad_json": {"he": "תשובת המודל אינה JSON תקין.", "en": "The model's answer is not valid JSON."},
    "llm.refine_skipped_heuristic": {"he": "לא נעשה שימוש במודל שפה (מצב היוריסטי).", "en": "No language model was used (heuristic mode)."},
    "llm.refine_skipped_no_transcript": {"he": "אין תמלול – מודל השפה לא הופעל.", "en": "No transcript – the language model was not run."},
    "llm.refine_failed_detail": {"he": "{error} המשכנו עם הכותרות ההיוריסטיות.", "en": "{error} Continued with the heuristic titles."},
    "llm.refine_failed": {"he": "מודל השפה נכשל. המשכנו עם הכותרות ההיוריסטיות.", "en": "The language model failed. Continued with the heuristic titles."},
    "llm.refined": {"he": "מודל השפה שיפר {count} כותרות ותיאורים.", "en": "The language model improved {count} titles and descriptions."},
    "llm.discover_failed": {"he": "גילוי רגעים באמצעות מודל שפה נכשל; נעשה שימוש באותות בלבד.",
                            "en": "Finding moments with the language model failed; only signals were used."},
    "llm.discovered": {"he": "מודל השפה הציע {count} רגעים נוספים מתוך התמלול.",
                       "en": "The language model suggested {count} more moments from the transcript."},

    # ---- תמלול ----
    "transcribe.fixture_missing": {"he": "ספק הבדיקה לא מצא קובץ תמלול.", "en": "The test provider did not find a transcript file."},
    "transcribe.fixture_hint": {"he": "צפוי: {name}", "en": "Expected: {name}"},
    "transcribe.no_whisper": {"he": "הספרייה faster-whisper אינה מותקנת.", "en": "The faster-whisper library is not installed."},
    "transcribe.no_whisper_hint": {"he": "הרץ: pip install faster-whisper", "en": "Run: pip install faster-whisper"},
    "transcribe.download_failed": {"he": "לא ניתן להוריד את מודל התמלול '{model}'.", "en": "The transcription model '{model}' cannot be downloaded."},
    "transcribe.download_hint": {
        "he": "נדרשת גישה לאינטרנט בהורדה הראשונה. לאחר מכן המודל נשמר מקומית ב-{path}. אפשר גם להעתיק ידנית תיקיית מודל לשם.",
        "en": "Internet access is needed for the first download. After that the model is stored locally in {path}. You can also copy a model folder there by hand."},
    "transcribe.gpu_memory": {"he": "אין מספיק זיכרון GPU לטעינת המודל.", "en": "There is not enough GPU memory to load the model."},
    "transcribe.gpu_memory_hint": {"he": "בחר מודל קטן יותר, או העבר את המכשיר ל-CPU בהגדרות.", "en": "Choose a smaller model, or switch the device to CPU in Settings."},
    "transcribe.load_failed": {"he": "טעינת מודל התמלול נכשלה.", "en": "Loading the transcription model failed."},
    "transcribe.failed": {"he": "התמלול נכשל.", "en": "Transcription failed."},
    "transcribe.failed_midway": {"he": "התמלול נכשל באמצע.", "en": "Transcription failed partway through."},

    # ---- FFmpeg ----
    "ffmpeg.no_ffprobe": {"he": "FFprobe לא נמצא במערכת.", "en": "FFprobe was not found on this system."},
    "ffmpeg.no_ffprobe_hint": {"he": "FFprobe מגיע יחד עם FFmpeg. ודא שהתיקייה ב-PATH.", "en": "FFprobe comes with FFmpeg. Make sure its folder is on PATH."},
    "ffmpeg.file_missing": {"he": "הקובץ לא נמצא: {name}", "en": "File not found: {name}"},
    "ffmpeg.unreadable": {"he": "לא ניתן לקרוא את קובץ הווידאו (ייתכן שהוא פגום).", "en": "The video file cannot be read (it may be corrupt)."},
    "ffmpeg.bad_probe": {"he": "פלט ffprobe לא תקין.", "en": "Invalid ffprobe output."},
    "ffmpeg.failed": {"he": "עיבוד הווידאו נכשל.", "en": "Video processing failed."},
    "verify.not_created": {"he": "הקובץ לא נוצר", "en": "The file was not created"},
    "verify.too_small": {"he": "הקובץ ריק או קטן מדי", "en": "The file is empty or too small"},
    "verify.no_video": {"he": "אין זרם וידאו בקובץ", "en": "The file has no video stream"},
    "verify.zero_length": {"he": "אורך הקובץ אפסי", "en": "The file has zero length"},
    "verify.decode_failed": {"he": "פענוח נכשל: {detail}", "en": "Decoding failed: {detail}"},
    "verify.ok_warnings": {"he": "תקין (עם אזהרות: {detail})", "en": "OK (with warnings: {detail})"},
    "verify.ok": {"he": "תקין", "en": "OK"},

    # ---- דיסק ----
    "fs.no_unique_name": {"he": "לא ניתן ליצור שם קובץ ייחודי.", "en": "A unique file name cannot be created."},
    "fs.no_space": {"he": "אין מספיק מקום בדיסק: נדרשים כ-{needed}, פנויים {free}.",
                    "en": "Not enough disk space: about {needed} is needed, {free} is free."},

    # ---- ניתוח אודיו ווידאו ----
    "visual.missing": {"he": "קובץ הווידאו לא נמצא לניתוח חזותי.", "en": "The video file for visual analysis was not found."},
    "visual.no_video": {"he": "לא נמצא זרם וידאו בקובץ.", "en": "No video stream was found in the file."},
    "visual.failed": {"he": "הניתוח החזותי נכשל.", "en": "Visual analysis failed."},
    "visual.no_frames": {"he": "לא נקראו פריימים לניתוח.", "en": "No frames were read for analysis."},
    "audio.missing": {"he": "קובץ האודיו לא נוצר.", "en": "The audio file was not created."},
    "audio.bad_format": {"he": "פורמט אודיו לא צפוי (נדרש PCM 16-bit).", "en": "Unexpected audio format (16-bit PCM is required)."},
    "audio.empty": {"he": "קובץ האודיו ריק.", "en": "The audio file is empty."},
    "audio.too_short": {"he": "לא ניתן לנתח את האודיו (קצר מדי).", "en": "The audio cannot be analyzed (too short)."},

    # ---- רינדור ----
    "render.no_segments": {"he": "לא הוגדרו מקטעים לייצוא.", "en": "No segments were defined for export."},
    "render.no_source": {"he": "קובץ המקור לא נמצא.", "en": "The source file was not found."},
    "render.bad_output": {"he": "קובץ הפלט לא נוצר כראוי.", "en": "The output file was not created correctly."},

    # ---- ציר זמן (בדיקות שפיות) ----
    "timeline.end_before_start": {"he": "קטע {index}: סוף לפני התחלה", "en": "Segment {index}: ends before it starts"},
    "timeline.bad_speed": {"he": "קטע {index}: מהירות לא חוקית {speed}", "en": "Segment {index}: invalid speed {speed}"},
    "timeline.overlap": {"he": "קטע {index}: חופף לקטע שלפניו", "en": "Segment {index}: overlaps the previous segment"},
    "timeline.bad_duration": {"he": "הכנסה {index}: משך לא חוקי", "en": "Insert {index}: invalid duration"},
    "timeline.negative_time": {"he": "הכנסה {index}: זמן שלילי", "en": "Insert {index}: negative time"},
    "timeline.past_end": {"he": "הכנסה {index}: מעבר לסוף העריכה ({at} > {end})", "en": "Insert {index}: past the end of the edit ({at} > {end})"},
}
