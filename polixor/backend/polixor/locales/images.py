"""יצירת תמונות: ספקים, התקדמות, הודעות שגיאה מפורטות ושרשרת ההכנסה לווידאו."""

NAMESPACE = "images"

MESSAGES = {
    # ---- ספקים ----
    "provider.base": {"he": "בסיס", "en": "Base"},
    "provider.openai": {"he": "OpenAI Images", "en": "OpenAI Images"},
    "provider.placeholder": {"he": "כרטיס מקומי (לא AI)", "en": "Local card (not AI)"},
    "unavailable.not_implemented": {"he": "לא ממומש", "en": "Not implemented"},
    "unavailable.no_key": {"he": "לא הוגדר מפתח API של OpenAI.", "en": "No OpenAI API key is configured."},
    "unavailable.no_httpx": {"he": "הספרייה httpx אינה מותקנת.", "en": "The httpx library is not installed."},
    "unavailable.no_pillow": {"he": "הספרייה Pillow אינה מותקנת.", "en": "The Pillow library is not installed."},

    # ---- התקדמות ----
    "progress.sending": {"he": "שולח את הפרומפט לספק…", "en": "Sending the prompt to the provider…"},
    "progress.downloading": {"he": "מוריד את התמונה…", "en": "Downloading the image…"},
    "progress.variation": {"he": "שולח את התמונה לווריאציה…", "en": "Sending the image for a variation…"},
    "progress.local_card": {"he": "מצייר כרטיס מקומי…", "en": "Drawing a local card…"},
    "progress.retry": {"he": "ניסיון {attempt} מתוך {attempts} בעוד {seconds} שניות…",
                       "en": "Attempt {attempt} of {attempts} in {seconds} seconds…"},
    "progress.analyzing": {"he": "מנתח את הפרומפט…", "en": "Analyzing the prompt…"},

    # ---- הערות ----
    "note.variation_regenerated": {
        "he": "וריאציה נוצרה על-ידי יצירה מחדש מאותו פרומפט – הדגם הנוכחי אינו תומך בעריכת תמונה קיימת.",
        "en": "The variation was made by generating again from the same prompt – the current model does not support editing an existing image."},
    "note.edit_failed": {"he": "עריכת התמונה נכשלה; נוצרה תמונה חדשה מאותו פרומפט.",
                         "en": "Editing the image failed; a new image was created from the same prompt."},
    "note.local_card": {"he": "כרטיס מקומי – לא נוצר על-ידי מודל בינה מלאכותית.",
                        "en": "Local card – not created by an AI model."},

    # ---- שגיאות מפורטות ----
    "error.unreachable": {"he": "לא ניתן להגיע לשרת של OpenAI.", "en": "The OpenAI server cannot be reached."},
    "error.unreachable_hint": {"he": "בדוק את החיבור לרשת ואת הגדרות ה-proxy.", "en": "Check the network connection and proxy settings."},
    "error.key_rejected": {"he": "מפתח ה-API של OpenAI נדחה.", "en": "The OpenAI API key was rejected."},
    "error.key_rejected_hint": {"he": "בדוק שהמפתח נכון ופעיל במסך ההגדרות.", "en": "Check in Settings that the key is correct and active."},
    "error.bad_params": {"he": "ספק התמונות דחה את הפרומפט או את הפרמטרים.", "en": "The image provider rejected the prompt or the parameters."},
    "error.provider_down": {"he": "ספק התמונות אינו זמין כרגע.", "en": "The image provider is not available right now."},
    "error.provider_down_hint": {"he": "נסה שוב בעוד רגע.", "en": "Try again in a moment."},
    "error.http": {"he": "ספק התמונות החזיר שגיאה {code}.", "en": "The image provider returned error {code}."},
    "error.no_image": {"he": "התשובה לא הכילה תמונה.", "en": "The response did not contain an image."},
    "error.corrupt": {"he": "נתוני התמונה פגומים.", "en": "The image data is corrupt."},
    "error.download_failed": {"he": "לא ניתן להוריד את התמונה שנוצרה.", "en": "The generated image could not be downloaded."},
    "error.empty": {"he": "התמונה שהתקבלה ריקה.", "en": "The received image is empty."},
    "error.prompt_short": {"he": "הפרומפט קצר מדי.", "en": "The prompt is too short."},
    "error.prompt_short_hint": {"he": "תאר מה אתה רוצה לראות בתמונה, לפחות כמה מילים.", "en": "Describe what you want to see in the image, at least a few words."},
    "error.prompt_long": {"he": "הפרומפט ארוך מדי ({length} תווים).", "en": "The prompt is too long ({length} characters)."},
    "error.prompt_long_hint": {"he": "המכסה היא {max} תווים.", "en": "The limit is {max} characters."},
    "error.bad_aspect": {"he": "יחס מסך לא נתמך: {aspect}", "en": "Unsupported aspect ratio: {aspect}"},
    "error.bad_aspect_hint": {"he": "יחסים נתמכים: {allowed}", "en": "Supported ratios: {allowed}"},

    # ---- שרשרת היצירה והשיבוץ ----
    "status.cancelled": {"he": "היצירה בוטלה.", "en": "Creation was cancelled."},
    "status.ready": {"he": "התמונה מוכנה", "en": "The image is ready"},
    "status.crashed": {"he": "יצירת התמונה נכשלה מסיבה לא צפויה.", "en": "Creating the image failed for an unexpected reason."},
    "status.failed": {"he": "יצירת התמונה נכשלה: {error}", "en": "Creating the image failed: {error}"},
    "status.save_failed": {"he": "שמירת התמונה נכשלה: {error}", "en": "Saving the image failed: {error}"},
    "error.bad_role": {"he": "תפקיד תמונה לא מוכר: {role}", "en": "Unknown image role: {role}"},
    "error.bad_role_hint": {"he": "תפקידים נתמכים: {allowed}", "en": "Supported roles: {allowed}"},
    "error.no_room": {"he": "אין מספיק זמן בקליפ לשיבוץ בנקודה הזו.", "en": "There is not enough time in the clip to place the image at this point."},
    "error.no_room_hint": {"he": "בחר נקודה מוקדמת יותר או משך קצר יותר.", "en": "Choose an earlier point or a shorter duration."},

    # ---- הכנסה לווידאו ----
    "render.still_failed": {"he": "יצירת קטע וידאו מהתמונה נכשלה.", "en": "Creating a video segment from the image failed."},
    "render.no_parts": {"he": "אין קטעים לחיבור.", "en": "There are no segments to join."},
    "render.concat_failed": {"he": "חיבור הקטעים עם התמונות נכשל.", "en": "Joining the segments with the images failed."},
    "render.missing": {"he": "תמונה חסרה: {name}", "en": "Missing image: {name}"},
    "render.overlay_failed": {"he": "החלת שכבות התמונה נכשלה.", "en": "Applying the image layers failed."},
    "render.summary_layers": {"he": "{count} שכבות", "en": "{count} layers"},
    "render.summary_timeline": {"he": "{count} תמונות בציר הזמן (+{seconds} שניות)", "en": "{count} images in the timeline (+{seconds} seconds)"},
}
