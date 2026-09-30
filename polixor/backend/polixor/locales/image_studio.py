"""סטודיו התמונות (services/image_studio)."""

NAMESPACE = "image_studio"

MESSAGES = {
    "error.thread_missing": {"he": "השיחה לא נמצאה.", "en": "Conversation not found."},
    "error.message_missing": {"he": "ההודעה לא נמצאה.", "en": "Message not found."},
    "error.bad_mode": {"he": "מצב לא מוכר. אפשר: אוטומטי, תמונה חדשה או עריכה.",
                       "en": "Unknown mode. Use auto, new image or edit."},
    "error.bad_background": {"he": "סוג רקע לא מוכר.", "en": "Unknown background option."},
    "error.no_transparent": {"he": "המודל שנבחר לא תומך ברקע שקוף.",
                             "en": "The selected model doesn't support a transparent background."},
    "error.nothing_to_edit": {"he": "אין עדיין תמונה בשיחה הזו לערוך. תארו קודם מה ליצור.",
                              "en": "There's no image in this conversation to edit yet. Describe what to create first."},
    "error.no_edit": {"he": "המודל {model} לא תומך בעריכת תמונות או בתמונות ייחוס. בחרו מודל אחר בהגדרות.",
                      "en": "The {model} model can't edit images or use reference images. Pick another model in Settings."},
    "error.too_many_refs": {"he": "אפשר לשלוח עד {max} תמונות בבקשה אחת (כולל התמונה שעורכים).",
                            "en": "You can send up to {max} images in one request (including the image being edited)."},
    "error.attachment_missing": {"he": "אחת התמונות המצורפות לא נמצאה או עדיין לא מוכנה.",
                                 "en": "One of the attached images wasn't found or isn't ready yet."},
    "error.upload_empty": {"he": "הקובץ ריק.", "en": "The file is empty."},
    "error.upload_too_big": {"he": "הקובץ גדול מדי (עד {mb}MB).", "en": "The file is too large (up to {mb} MB)."},
    "error.upload_type": {"he": "אפשר לצרף רק PNG, JPEG או WEBP.", "en": "Only PNG, JPEG or WEBP images can be attached."},
    "error.upload_invalid": {"he": "הקובץ אינו תמונה תקינה.", "en": "The file isn't a valid image."},
}
