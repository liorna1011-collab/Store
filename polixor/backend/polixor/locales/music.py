"""מוזיקת רקע: פרופילים, עקומת העוצמה והסבר לכל קטע."""

NAMESPACE = "music"

MESSAGES = {
    "profile.minimal.label": {"he": "מינימלי", "en": "Minimal"},
    "profile.minimal.description": {"he": "מוזיקה שקטה שכמעט לא מורגשת. הדיבור הוא העיקר.",
                                    "en": "Quiet music that is barely noticeable. The speech comes first."},
    "profile.balanced.label": {"he": "מאוזן", "en": "Balanced"},
    "profile.balanced.description": {"he": "נוכחות מורגשת בהפסקות, יורדת בבירור מתחת לדיבור.",
                                     "en": "Noticeable in pauses, clearly ducked under speech."},
    "profile.energetic.label": {"he": "אנרגטי", "en": "Energetic"},
    "profile.energetic.description": {"he": "מוזיקה חזקה יותר, לסרטונים מהירים. עדיין לא מתחרה בקול.",
                                      "en": "Louder music for fast videos. Still does not compete with the voice."},

    "note.no_file": {"he": "לא נבחר קובץ מוזיקה. המערכת אינה מספקת מוזיקה — יש לבחור קובץ שיש לך זכות להשתמש בו.",
                     "en": "No music file was chosen. The app does not supply music – choose a file you have the right to use."},
    "note.no_duration": {"he": "אורך הקליפ אינו ידוע — לא נבנתה עקומה.", "en": "The clip length is unknown – no curve was built."},
    "note.unmeasured": {
        "he": "לא ניתן היה למדוד את עוצמת קובץ המוזיקה; מונח שהוא בסביבות {lufs} LUFS. אם הוא יישמע חזק או חלש מדי, כוונן את הפרופיל.",
        "en": "The loudness of the music file could not be measured; it is assumed to be around {lufs} LUFS. If it sounds too loud or too quiet, adjust the profile."},
    "note.no_transcript": {"he": "בלי תמלול אי אפשר להתאים את העוצמה למבנה הסיפור.",
                           "en": "Without a transcript the level cannot follow the structure of the story."},
    "note.fades": {"he": "המוזיקה נכנסת ב-{fade_in} שנ' ויוצאת ב-{fade_out} שנ', כדי שהסיום ייסגר ולא ייחתך.",
                   "en": "The music fades in over {fade_in} s and out over {fade_out} s, so the ending resolves instead of cutting off."},

    "cue.no_transcript": {"he": "אין תמלול — המוזיקה רצה בעוצמה אחידה ונמוכה, כדי שלא תתחרה בדיבור שלא נותח.",
                          "en": "No transcript – the music runs at an even, low level so it does not compete with unanalyzed speech."},
    "cue.short": {"he": "קטע קצר — עוצמה אחידה.", "en": "A short section – even level."},
    "cue.peak": {"he": "שיא רגשי — המוזיקה עולה ומחזקת את הרגע.", "en": "Emotional peak – the music rises and strengthens the moment."},
    "cue.hook": {"he": "פתיח — עוצמה בינונית שמושכת בלי להסתיר את המילים.", "en": "Opening – a medium level that draws in without hiding the words."},
    "cue.key": {"he": "כאן נאמרות המילים החשובות — המוזיקה יורדת כדי שיישמעו.", "en": "The important words are said here – the music drops so they are heard."},
    "cue.filler": {"he": "קטע חלש — המוזיקה מונמכת.", "en": "A weak section – the music is lowered."},
    "cue.body": {"he": "גוף הסיפור — המוזיקה נמוכה ומלווה בלבד.", "en": "Body of the story – the music is low and only accompanies."},
    "cue.default": {"he": "ליווי בעוצמה רגילה.", "en": "Accompaniment at normal level."},
    "cue.gap": {"he": "מעבר — עוצמה נמוכה.", "en": "Transition – low level."},
    "cue.end": {"he": "סיום הקליפ.", "en": "End of the clip."},

    "ducking.missing": {"he": "קובץ המוזיקה המעובד לא נמצא.", "en": "The processed music file was not found."},
    "ducking.read_failed": {"he": "קריאת הקובץ נכשלה: {error}", "en": "Reading the file failed: {error}"},
    "ducking.empty": {"he": "הקובץ ריק.", "en": "The file is empty."},
    "ducking.not_enough": {"he": "אין מספיק דיבור או מספיק הפסקות כדי להשוות.", "en": "There is not enough speech or enough pauses to compare."},
}
