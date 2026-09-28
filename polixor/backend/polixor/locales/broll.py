"""החלטות חומר נלווה (B-roll): מתי הוא עוזר, ומתי הדובר עדיף."""

NAMESPACE = "broll"

MESSAGES = {
    "role.hook": {"he": "פתיח", "en": "Opening"},
    "role.emotional_peak": {"he": "שיא רגשי", "en": "Emotional peak"},
    "role.payoff": {"he": "פאנץ'", "en": "Payoff"},
    "role.cta": {"he": "קריאה לפעולה", "en": "Call to action"},
    "category.place": {"he": "תיאור מקום", "en": "A description of a place"},
    "category.object": {"he": "אובייקט מוחשי", "en": "A tangible object"},
    "category.action": {"he": "פעולה פיזית", "en": "A physical action"},
    "category.nature": {"he": "תיאור סביבה", "en": "A description of surroundings"},
    "category.default": {"he": "תיאור חזותי", "en": "A visual description"},

    "note.no_transcript": {"he": "אין תמלול — אי אפשר להחליט על חומר נלווה.", "en": "No transcript – B-roll cannot be decided."},
    "note.style_none": {
        "he": "{count} משפטים היו מתאימים לחומר נלווה, אבל הסגנון הנוכחי אינו מקצה לו מקום.",
        "en": "{count} sentences were suitable for B-roll, but the current style gives it no room."},
    "note.reused": {
        "he": "{reused} מתוך {total} ההכנסות משתמשות בחומר שכבר קיים — זול יותר, מהיר יותר ועקבי יותר ויזואלית.",
        "en": "{reused} of {total} inserts use existing material – cheaper, faster and more visually consistent."},
    "note.limited": {
        "he": "נבחרו {chosen} הכנסות מתוך {total} מועמדות. חומר נלווה על כל משפט מרחיק את הצופה מהדובר.",
        "en": "{chosen} inserts were chosen out of {total} candidates. B-roll on every sentence distances the viewer from the speaker."},

    "reason.style_none": {"he": "הסגנון הזה לא מקצה חומר נלווה — הסרטון נשאר על הדובר.",
                          "en": "This style allocates no B-roll – the video stays on the speaker."},
    "reason.over_budget": {"he": " · לא נבחר: נגמרה מכסת החומר הנלווה של הסגנון", "en": " · not chosen: the style's B-roll budget ran out"},
    "reason.too_close": {"he": " · לא נבחר: קרוב מדי להכנסה אחרת (מרווח מינימלי {gap} שנ')",
                         "en": " · not chosen: too close to another insert (minimum gap {gap} s)"},
    "reason.direct_address": {"he": "{role}: פנייה ישירה לצופה. חיתוך מהפנים כאן שובר את הקשר.",
                              "en": "{role}: speaking directly to the viewer. Cutting away from the face here breaks the connection."},
    "reason.meta": {"he": "המשפט מדבר על הסרטון עצמו ולא על משהו שאפשר לצלם — תמונה כאן תהיה קישוט.",
                    "en": "The sentence is about the video itself, not something that can be filmed – a picture here would be decoration."},
    "reason.emotion": {"he": "{role}: הרגש נישא בהבעה ובקול, ואין כאן תיאור חזותי חזק מספיק שיצדיק חיתוך מהדובר.",
                       "en": "{role}: the emotion is carried by expression and voice, and there is no visual description strong enough to justify cutting away from the speaker."},
    "reason.pacing": {"he": "תכנית הקצב אינה מאפשרת חומר נלווה כאן: {reason}", "en": "The pacing plan does not allow B-roll here: {reason}"},
    "reason.too_short": {"he": "המשפט קצר מדי ({seconds} שנ') — הכנסה כאן תיראה כמו הבהוב.",
                         "en": "The sentence is too short ({seconds} s) – an insert here would look like a flicker."},
    "reason.not_visual": {"he": "אין כאן תיאור שאפשר לצלם — {abstract}תמונה תהיה גנרית ולא תוסיף מידע.",
                          "en": "There is nothing here that can be filmed – {abstract}a picture would be generic and add no information."},
    "reason.abstract": {"he": "ניסוח של דעה או רעיון. ", "en": "it is an opinion or an idea. "},
    "reason.existing": {"he": "{category} במשפט, ויש כבר חומר מתאים ({match}% התאמה) — עדיף על יצירה חדשה.",
                        "en": "{category} in the sentence, and matching material already exists ({match}% match) – better than creating something new."},
    "reason.generate": {"he": "{category} במשפט — חומר נלווה כאן מראה את מה שנאמר במקום להסביר אותו.",
                        "en": "{category} in the sentence – B-roll here shows what is said instead of explaining it."},
    "reason.serves_emotion": {"he": " {role} עם תיאור חזותי חזק — החומר משרת את הרגש ולא מחליף אותו.",
                              "en": " {role} with a strong visual description – the material serves the emotion rather than replacing it."},
}
