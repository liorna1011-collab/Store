"""פריסטים של מנוע הכתוביות והערות על הדגשת מילים."""

NAMESPACE = "captions"

MESSAGES = {
    "preset.clean.label": {"he": "נקי", "en": "Clean"},
    "preset.clean.description": {
        "he": "שתי שורות שקטות בתחתית. הכתובית משרתת את הדיבור ולא מושכת אליה תשומת לב.",
        "en": "Two quiet lines at the bottom. The caption serves the speech and does not draw attention to itself."},
    "preset.viral.label": {"he": "ויראלי", "en": "Viral"},
    "preset.viral.description": {
        "he": "שורה אחת, עד שלוש מילים, הדגשה רצה על המילה הנאמרת. מיועד לפיד אנכי מהיר.",
        "en": "One line, up to three words, a running highlight on the spoken word. For a fast vertical feed."},
    "preset.cinematic.label": {"he": "קולנועי", "en": "Cinematic"},
    "preset.cinematic.description": {
        "he": "כתובית קטנה ומאופקת, בלי אנימציה. התמונה היא העיקר והכתובית רק מלווה.",
        "en": "A small, restrained caption without animation. The picture comes first and the caption only accompanies it."},
    "preset.podcast.label": {"he": "פודקאסט", "en": "Podcast"},
    "preset.podcast.description": {
        "he": "שיחה ארוכה: כתוביות בינוניות, שבירה בכל מעבר דובר, וצבע לכל דובר כשיש תוויות.",
        "en": "A long conversation: medium captions, a break at every speaker change, and a color per speaker when labels exist."},
    "preset.story.label": {"he": "סיפור", "en": "Story"},
    "preset.story.description": {
        "he": "כתובית במרכז הפריים, קצרה, עם קצב נשימה של סיפור בגוף ראשון.",
        "en": "A short caption in the middle of the frame, with the breathing rhythm of a first-person story."},

    "emphasis.none": {"he": "הפריסט הנוכחי אינו מדגיש מילים.", "en": "The current preset does not emphasize words."},
    "emphasis.limited": {
        "he": "הודגשו {applied} מילים מתוך {requested} — מעל {per_minute} לדקה ההדגשה מאבדת את כוחה.",
        "en": "{applied} of {requested} words were emphasized – above {per_minute} per minute, emphasis loses its power."},
    "emphasis.unmatched": {
        "he": "{count} הדגשות לא הוחלו: המילה בכתובית לא תאמה למילה שבהחלטה.",
        "en": "{count} emphases were not applied: the word in the caption did not match the word in the decision."},
}
