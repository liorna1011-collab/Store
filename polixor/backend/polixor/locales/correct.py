"""הסברים של שלב ההגהה (services/transcript_correct)."""

NAMESPACE = "correct"

MESSAGES = {
    "reason.confirmed": {
        "he": "התמלול החוזר במודל החזק שמע אותו דבר – המקור אושר.",
        "en": "The stronger model heard the same thing – the original is confirmed."},
    "reason.retranscription": {
        "he": "תוקן לפי תמלול חוזר של הקטע במודל חזק יותר (ביטחון {confidence}%, שיפור {gain} נק').",
        "en": "Corrected from a re-transcription of this part with a stronger model ({confidence}% confidence, {gain} pts better)."},
    "reason.retranscription_vocab": {
        "he": "תוקן לפי תמלול חוזר שזיהה מונח מרשימת השמות והמונחים: {terms}.",
        "en": "Corrected from a re-transcription that recognized a term from your names/terms list: {terms}."},
    "reason.vocabulary": {
        "he": "מילה לא בטוחה הוחלפה במונח מרשימת השמות והמונחים ({terms}) בכתיב קרוב מאוד.",
        "en": "An uncertain word was replaced with a very close term from your names/terms list ({terms})."},
    "reason.llm_choice": {
        "he": "מודל השפה בחר, לפי ההקשר, בחלופה שנשמעה בתמלול החוזר (לא טקסט חדש).",
        "en": "The language model chose, from context, the alternative heard in the re-transcription (no new text)."},
    "reason.cloud_confirmed": {
        "he": "תמלול נוסף בענן שמע אותו דבר כמו המקור – המקור נשאר.",
        "en": "An extra cloud transcription heard the same as the original – the original is kept."},
    "reason.cloud_agrees": {
        "he": "תוקן: התמלול החוזר והתמלול בענן שמעו את אותה חלופה.",
        "en": "Corrected: the re-transcription and the cloud transcription heard the same alternative."},
    "reason.cloud": {
        "he": "תוקן לפי תמלול בענן של הקטע (ביטחון {confidence}%, גבוה בבירור מהמקור).",
        "en": "Corrected from a cloud transcription of this part ({confidence}% confidence, clearly above the original)."},
    "reason.alternative_weak": {
        "he": "יש חלופה מהתמלול החוזר, אבל הראיה לא חזקה מספיק – מסומן לבדיקה ידנית.",
        "en": "The re-transcription offers an alternative, but the evidence is not strong enough – flagged for manual review."},
    "reason.no_alternative": {
        "he": "התמלול החוזר לא שמע כאן דיבור ברור – מסומן לבדיקה ידנית.",
        "en": "The re-transcription heard no clear speech here – flagged for manual review."},
    "reason.low_confidence": {
        "he": "זיהוי לא בטוח – מסומן לבדיקה ידנית.",
        "en": "Uncertain recognition – flagged for manual review."},
    "note.summary": {
        "he": "הגהת כתוביות: {checked} משפטים לא בטוחים נבדקו בקליפים שנבחרו – {corrected} תוקנו לפי ראיה מהאודיו, {confirmed} אושרו, {flagged} סומנו לבדיקה ידנית בעורך.",
        "en": "Subtitle proofreading: {checked} uncertain sentences in the chosen clips were checked – {corrected} corrected from audio evidence, {confirmed} confirmed, {flagged} flagged for manual review in the editor."},
    "note.timing": {
        "he": "תזמון כתוביות: {words} מילים יושרו לדיבור באודיו; {dropped} משפטים שזוהו בשקט הוסרו; {flagged} סומנו לבדיקה.",
        "en": "Subtitle timing: {words} words were aligned to the speech in the audio; {dropped} sentences detected in silence were removed; {flagged} were flagged for review."},
    "note.alignment_missing": {
        "he": "יישור כפוי מופעל בהגדרות, אבל הרכיבים שלו לא מותקנים (requirements-alignment.txt). הזמנים תוקנו לפי האודיו בלבד.",
        "en": "Forced alignment is on in Settings, but its components are not installed (requirements-alignment.txt). Timing was repaired from the audio only."},
    "note.strong_unavailable": {
        "he": "המודל החזק לתמלול חוזר ({model}) אינו זמין (לא ניתן היה להוריד אותו). משפטים לא בטוחים סומנו לבדיקה ידנית בלי תיקון.",
        "en": "The stronger re-transcription model ({model}) is unavailable (it could not be downloaded). Uncertain sentences were flagged for manual review without correction."},
    "reason.strong_checked": {
        "he": "פתיחת קליפ: נבדק שוב במודל החזק; המקור נשאר כי החלופה לא הייתה טובה ממנו בבירור.",
        "en": "Clip opening: re-checked with the stronger model; the original was kept because the alternative was not clearly better."},
    "note.strong_openings": {
        "he": "המודל החזק ({model}) בדק מחדש את הפתיחות של {clips} הקליפים שנבחרו ({checked} משפטים, {seconds} שניות עיבוד).",
        "en": "The stronger model ({model}) re-checked the openings of the {clips} chosen clips ({checked} sentences, {seconds} s of processing)."},
    "note.strong_windows": {
        "he": "המודל החזק ({model}) תמלל מחדש {windows} קטעים מבטיחים ({seconds} שניות אודיו, {wall} שניות עיבוד), והבחירה נעשתה על הטקסט המדויק.",
        "en": "The stronger model ({model}) re-transcribed {windows} promising parts ({seconds} s of audio, {wall} s of processing); clips were chosen from that accurate text."},
    "note.budget": {
        "he": "{n} קטעים לא נבדקו מחדש כדי לעמוד בתקציב הזמן של המצב המהיר; הם מסומנים לבדיקה ידנית.",
        "en": "{n} parts were not re-transcribed to stay within the fast-mode time budget; they are flagged for manual review."},
}
