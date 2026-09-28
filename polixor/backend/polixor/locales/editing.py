"""סגנונות עריכה, הערות תכנית העריכה ותיאור מה שהעורך עשה."""

NAMESPACE = "editing"

MESSAGES = {
    # ---- סגנונות ----
    "style.raw.label": {"he": "גולמי", "en": "Raw"},
    "style.raw.description": {
        "he": "חיתוך ישיר מהשידור, בלי שום עריכה. להשוואה.",
        "en": "A straight cut from the stream, with no editing at all. For comparison."},
    "style.clean.label": {"he": "נקי", "en": "Clean"},
    "style.clean.description": {
        "he": "מסיר אוויר מת, מהדק את ההתחלה והסוף, ומלטש אודיו. נשאר נאמן למקור.",
        "en": "Removes dead air, tightens the start and end, and polishes the audio. Stays true to the source."},
    "style.dynamic.label": {"he": "דינמי", "en": "Dynamic"},
    "style.dynamic.description": {
        "he": "בנוסף: שינוי זווית בכל חיתוך, דחיפה על רגע השיא, וצבע מעט חזק יותר. זה מה שמרגיש 'ערוך'.",
        "en": "Adds an angle change on every cut, a push-in at the peak and slightly stronger color. This is what feels “edited”."},
    "style.hype.label": {"he": "אנרגטי", "en": "Hype"},
    "style.hype.description": {
        "he": "הכי הדוק: הסרה אגרסיבית של שקט, הרבה שינויי זווית, האצה על קטעים מתים וכתוביות קופצות. לשורטים.",
        "en": "The tightest: aggressive silence removal, many angle changes, speed-up on dead stretches and popping captions. For shorts."},

    # ---- סיבות לביטים ----
    "beat.raw": {"he": "חיתוך ישיר", "en": "Straight cut"},
    "beat.dramatic": {"he": "קטע + שתיקה דרמטית", "en": "Segment + dramatic pause"},
    "beat.speech": {"he": "קטע דיבור", "en": "Speech segment"},
    "beat.last": {"he": "קטע אחרון", "en": "Last segment"},
    "beat.uncut": {"he": "ללא חיתוכים", "en": "No cuts"},
    "beat.peak_push": {"he": " · דחיפה על השיא", "en": " · push-in at the peak"},
    "beat.push_step": {"he": " · דחיפה {step}/{steps}", "en": " · push-in {step}/{steps}"},
    "beat.sped_up": {"he": " · מואץ (ללא דיבור)", "en": " · sped up (no speech)"},

    # ---- הערות התכנית ----
    "note.raw": {"he": "סגנון גולמי: לא בוצעה עריכה.", "en": "Raw style: no editing was done."},
    "note.trim": {
        "he": "הידוק ראש/זנב: הוסרו {head} שנ' בהתחלה ו-{tail} שנ' בסוף.",
        "en": "Head/tail tightening: removed {head} s at the start and {tail} s at the end."},
    "note.beats_capped": {
        "he": "מספר החיתוכים הוגבל ל-{max} לשמירה על קצב נעים.",
        "en": "The number of cuts was capped at {max} to keep a comfortable pace."},
    "note.removed": {
        "he": "הוסרו {seconds} שניות של אוויר מת ב-{cuts} חיתוכים.",
        "en": "Removed {seconds} seconds of dead air in {cuts} cuts."},
    "note.removed_dramatic": {
        "he": "הוסרו {seconds} שניות של אוויר מת ב-{cuts} חיתוכים, תוך שמירה על {dramatic} שתיקות דרמטיות.",
        "en": "Removed {seconds} seconds of dead air in {cuts} cuts, keeping {dramatic} dramatic pauses."},

    # ---- תיאור התכנית ----
    "describe.raw": {"he": "חיתוך ישיר מהשידור, ללא עריכה.", "en": "A straight cut from the stream, without editing."},
    "describe.style": {"he": "סגנון {label}", "en": "{label} style"},
    "describe.cuts": {"he": "{count} חיתוכים", "en": "{count} cuts"},
    "describe.removed": {
        "he": "הוסרו {seconds} שנ' אוויר מת ({percent}%)",
        "en": "removed {seconds} s of dead air ({percent}%)"},
    "describe.dramatic": {"he": "{count} שתיקות דרמטיות נשמרו", "en": "{count} dramatic pauses kept"},
    "describe.angles": {"he": "{count} שינויי זווית", "en": "{count} angle changes"},
    "describe.sped": {"he": "{count} קטעים מואצים", "en": "{count} sped-up segments"},
}
