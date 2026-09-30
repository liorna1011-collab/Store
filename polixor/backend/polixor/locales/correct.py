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
    "note.strong_unavailable": {
        "he": "המודל החזק לתמלול חוזר ({model}) אינו זמין (לא ניתן היה להוריד אותו). משפטים לא בטוחים סומנו לבדיקה ידנית בלי תיקון.",
        "en": "The stronger re-transcription model ({model}) is unavailable (it could not be downloaded). Uncertain sentences were flagged for manual review without correction."},
    "note.budget": {
        "he": "{n} קטעים לא נבדקו מחדש כדי לעמוד בתקציב הזמן של המצב המהיר; הם מסומנים לבדיקה ידנית.",
        "en": "{n} parts were not re-transcribed to stay within the fast-mode time budget; they are flagged for manual review."},
}
