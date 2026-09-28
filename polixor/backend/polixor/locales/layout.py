"""הסברים על מסגור ופריסה (Reframe / Facecam)."""

NAMESPACE = "layout"

MESSAGES = {
    "blur": {
        "he": "הפריים המלא במרכז על רקע מטושטש – שום דבר לא נחתך, והחדות נשמרת.",
        "en": "The full frame is centred on a blurred background – nothing is cropped and sharpness is kept."},
    "center": {
        "he": "חיתוך מרכזי קבוע.",
        "en": "Fixed centre crop."},
    "split": {
        "he": "מסך מפוצל: מצלמה למעלה, גיימפליי למטה.",
        "en": "Split screen: camera on top, gameplay below."},
    "split_no_camera": {
        "he": "לא זוהה אזור מצלמה – בוצע חיתוך מרכזי במקום מסך מפוצל. אפשר להגדיר את אזור המצלמה ידנית במסך העריכה.",
        "en": "No camera area was found – a centre crop was used instead of a split screen. You can set the camera area manually in the editor."},
    "no_faces": {
        "he": "לא זוהו פנים בקטע – בוצע חיתוך מרכזי.",
        "en": "No faces were found in this part – a centre crop was used."},
    "face_follow": {
        "he": "מעקב אחרי פנים ב-{percent}% מהקטע, {moves} קטעי תנועה.",
        "en": "Face tracking in {percent}% of the clip, {moves} movement segments."},
    "piece_reaction": {
        "he": "פריסת תגובה: התוכן והמצלמה מוצגים יחד, בלי לחתוך את הפנים.",
        "en": "Reaction layout: content and camera shown together, without cropping the face."},
    "piece_camera": {
        "he": "המצלמה ממלאת את הפריים – החיתוך עוקב אחרי הפנים.",
        "en": "The camera fills the frame – the crop follows the face."},
    "piece_screen_crop": {
        "he": "תוכן בלבד – חיתוך שמשאיר את רוב התוכן.",
        "en": "Content only – a crop that keeps most of the content."},
    "piece_screen_fit": {
        "he": "תוכן בלבד – מוצג במלואו על רקע מטושטש, כי חיתוך היה מאבד יותר מ-30% ממנו.",
        "en": "Content only – shown in full on a blurred background, since cropping would lose over 30% of it."},
    "pieces": {
        "he": "הפריסה מתחלפת {count} פעמים בקליפ: {kinds}.",
        "en": "The layout changes {count} times in this clip: {kinds}."},
    "kind_reaction": {"he": "תגובה", "en": "reaction"},
    "kind_camera": {"he": "מצלמה", "en": "camera"},
    "kind_screen": {"he": "מסך", "en": "screen"},
    "kind_center": {"he": "מרכז", "en": "centre"},
    "kind_blur": {"he": "רקע מטושטש", "en": "blurred background"},
    "kind_split": {"he": "מסך מפוצל", "en": "split screen"},
    "kind_face": {"he": "מעקב פנים", "en": "face tracking"},
    # תוויות לבורר הפריסה
    "option_auto": {"he": "אוטומטי", "en": "Automatic"},
    "option_reaction": {"he": "תגובה (תוכן + מצלמה)", "en": "Reaction (content + camera)"},
    "option_face": {"he": "מעקב פנים", "en": "Follow face"},
    "option_center": {"he": "מרכז", "en": "Centre"},
    "option_blur": {"he": "רקע מטושטש", "en": "Blurred background"},
    "detected_camera": {
        "he": "זוהה אזור מצלמת סטרימר (ביטחון {confidence}%). ניתן לתקן ידנית במסך העריכה.",
        "en": "A streamer camera area was detected ({confidence}% confidence). You can correct it in the editor."},
    "detected_facecam": {
        "he": "זוהתה מצלמת תגובה ב-{segments} קטעים ({seconds} שניות).",
        "en": "A reaction camera was detected in {segments} parts ({seconds} seconds)."},
    "detector_unavailable": {
        "he": "זיהוי הפריסה לא רץ: גלאי הפנים של OpenCV אינו זמין.",
        "en": "Layout detection did not run: OpenCV's face detector is unavailable."},
}
