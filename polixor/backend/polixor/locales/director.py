"""במאי ה-AI: נימוקי החלטות, קצב, הערכת הוו ותוויות פעולות."""

NAMESPACE = "director"

MESSAGES = {
    # ---- תכנית ----
    "plan.scope": {
        "he": "התכנית מכסה חיתוכים, מסגור, כתוביות והצעות ויזואליות. מוזיקה, סאונד דיזיין, מעברים ותיקון צבע אינם מתוכננים בשלב הזה.",
        "en": "The plan covers cuts, framing, captions and visual suggestions. Music, sound design, transitions and color correction are not planned at this stage."},
    "plan.no_transcript": {
        "he": "אין תמלול: אי אפשר לזהות מילות מילוי, תפקידים נרטיביים או מילים להדגשה. העריכה מתבססת על אותות אודיו בלבד.",
        "en": "No transcript: filler words, narrative roles and words to emphasize cannot be identified. Editing relies on audio signals only."},
    "segment.kept": {"he": "נשמר", "en": "Kept"},

    # ---- חיתוכים ----
    "cut.empty_opening": {"he": "פתיחה בלי תוכן", "en": "Opening without content"},
    "cut.kind.filler_word": {"he": "מילת מילוי", "en": "Filler word"},
    "cut.kind.filler_phrase": {"he": "ביטוי מילוי", "en": "Filler phrase"},
    "cut.kind.false_start": {"he": "התחלה כושלת", "en": "False start"},
    "cut.kind.repeat": {"he": "חזרה על מה שכבר נאמר", "en": "Repeats what was already said"},
    "cut.disfluency": {"he": "{kind}: „{text}”", "en": "{kind}: “{text}”"},
    "cut.filler_sentence": {"he": "משפט ללא תוכן חדש: „{text}”", "en": "A sentence with no new content: “{text}”"},
    "cut.meaningful_pause": {"he": "שתיקה שנושאת משמעות — נשמרת בכוונה", "en": "A meaningful pause – kept on purpose"},
    "cut.dead_air": {"he": "אוויר מת ({seconds} שניות ללא דיבור)", "en": "Dead air ({seconds} seconds without speech)"},
    "cut.over_budget": {"he": " · לא בוצע: נגמרה מכסת ההסרה של הסגנון", "en": " · not applied: the style's removal budget ran out"},
    "cut.skipped_note": {
        "he": "{count} הסרות אפשריות לא בוצעו כדי לא לעבור את תקרת ההסרה של הסגנון ({ratio}).",
        "en": "{count} possible removals were skipped to stay within the style's removal cap ({ratio})."},
    "sentence.trimmed_opening": {"he": "פתיחה ללא תוכן — נגזמת לפני הוו האמיתי", "en": "Opening without content – trimmed before the real hook"},

    # ---- מסגור ----
    "frame.no_zoom": {
        "he": "החיתוך ליחס היעד כבר מותח את המקור פי {base} ({src} → {dst}). זום נוסף היה מרכך את התמונה, ולכן המסגור נשאר קבוע.",
        "en": "Cropping to the target aspect already enlarges the source {base}× ({src} → {dst}). More zoom would soften the picture, so the framing stays fixed."},
    "frame.hold": {"he": "{role}: המסגור נשאר קבוע כאן", "en": "{role}: the framing stays fixed here"},
    "frame.zoom_cap": {
        "he": "תקרת הזום: {limit}×. החיתוך ליחס היעד כבר מותח פי {base}, והתקרה מוודאת שהמתיחה הכוללת לא עוברת את {max}×.",
        "en": "Zoom cap: {limit}×. Cropping to the target aspect already enlarges {base}×, and the cap keeps the total enlargement under {max}×."},
    "zoom.peak": {"he": "התקרבות איטית — הרגע הרגשי של הסרטון", "en": "A slow push-in – the emotional moment of the video"},
    "zoom.claim": {"he": "דחיפה פנימה על המשפט שנושא את המסר", "en": "A push-in on the sentence that carries the message"},
    "zoom.hook": {"he": "דחיפה קלה בפתיחה כדי לייצר תנועה מיד", "en": "A light push-in at the opening to create movement right away"},
    "zoom.topic": {"he": "יציאה החוצה — מסמנת מעבר לנושא חדש", "en": "A pull-out – marks a move to a new topic"},
    "zoom.cta": {"he": "התקרבות קלה בסיום, לפנייה ישירה לצופה", "en": "A light push-in at the end, to address the viewer directly"},
    "zoom.default": {"he": "שינוי מסגור קל כדי לשבור סטטיות", "en": "A slight reframe to break the static look"},

    # ---- הדגשות ----
    "emphasis.word": {"he": "המילה החזקה ב{role}: „{word}”", "en": "The strongest word in the {role}: “{word}”"},
    "emphasis.limited": {
        "he": "נבחרו {chosen} הדגשות מתוך {total} מועמדות — הדגשה על כל מילה שנייה מבטלת את האפקט.",
        "en": "{chosen} emphases were chosen out of {total} candidates – emphasizing every other word cancels the effect."},
    "broll.suggested": {
        "he": "{count} הצעות ויזואליות — אף אחת לא נוצרה ולא שובצה. יצירה ושיבוץ הן פעולות נפרדות של המשתמש.",
        "en": "{count} visual suggestions – none was created or placed. Creating and placing are separate user actions."},

    # ---- תוויות פעולות ----
    "action.cut": {"he": "הסרה", "en": "Removal"},
    "action.trim_head": {"he": "גיזום פתיחה", "en": "Opening trim"},
    "action.keep_pause": {"he": "שתיקה נשמרת", "en": "Pause kept"},
    "action.zoom_in": {"he": "התקרבות", "en": "Push-in"},
    "action.zoom_out": {"he": "התרחקות", "en": "Pull-out"},
    "action.hold_frame": {"he": "מסגור קבוע", "en": "Fixed framing"},
    "action.emphasize_word": {"he": "הדגשת מילה", "en": "Word emphasis"},
    "action.suggest_broll": {"he": "הצעת חומר נלווה", "en": "B-roll suggestion"},
    "action.move_hook": {"he": "הזזת הפתיח", "en": "Move the opening"},

    # ---- סגנונות קצב ----
    "pacing.clean_creator.label": {"he": "יוצר נקי", "en": "Clean creator"},
    "pacing.clean_creator.description": {
        "he": "טבעי ומקצועי. מעט אפקטים, שינוי ויזואלי כל 3–6 שניות.",
        "en": "Natural and professional. Few effects, a visual change every 3–6 seconds."},
    "pacing.viral_short.label": {"he": "שורט ויראלי", "en": "Viral short"},
    "pacing.viral_short.description": {
        "he": "מהיר. שינוי ויזואלי כל 1.5–3 שניות, הסרה אגרסיבית של שקט.",
        "en": "Fast. A visual change every 1.5–3 seconds, aggressive silence removal."},
    "pacing.cinematic_story.label": {"he": "סיפור קולנועי", "en": "Cinematic story"},
    "pacing.cinematic_story.description": {
        "he": "רגשי ואיטי. פחות חיתוכים, יותר שתיקות ותנועה עדינה.",
        "en": "Emotional and slow. Fewer cuts, more pauses and gentle movement."},
    "pacing.podcast_clip.label": {"he": "קטע פודקאסט", "en": "Podcast clip"},
    "pacing.podcast_clip.description": {
        "he": "כתוביות, מסגור מחדש ובי-רול קל. שומר על הדיבור.",
        "en": "Captions, reframing and light B-roll. Keeps the speech intact."},
    "pacing.educational.label": {"he": "הסברתי", "en": "Educational"},
    "pacing.educational.description": {
        "he": "המחשות בזמן הסברים, הדגשות על טענות.",
        "en": "Illustrations during explanations, emphasis on claims."},
    "pacing.product.label": {"he": "מוצר", "en": "Product"},
    "pacing.product.description": {
        "he": "פוקוס על המוצר: הרבה ויזואלים, פחות פנים.",
        "en": "Focus on the product: many visuals, fewer faces."},

    "pacing.no_transcript_reason": {"he": "אין תמלול — קצב אחיד לפי הסגנון בלבד", "en": "No transcript – an even pace from the style only"},
    "pacing.no_transcript_note": {
        "he": "בלי תמלול אי אפשר להתאים את הקצב לתפקיד הקטעים; הקצב אחיד לאורך כל הסרטון.",
        "en": "Without a transcript the pace cannot follow the role of each section; it stays even across the whole video."},
    "pacing.budget": {
        "he": "תקציב ויזואלים: {given} לאורך {seconds} שניות ({per_minute} לדקה בסגנון {style}).",
        "en": "Visual budget: {given} over {seconds} seconds ({per_minute} per minute in the {style} style)."},
    "pacing.too_short": {
        "he": "הסרטון קצר מכדי לשינוי ויזואלי בסגנון הזה — נשאר רצף אחד.",
        "en": "The video is too short for a visual change in this style – it stays one continuous shot."},
    "pacing.single_change": {"he": " · שינוי יחיד כדי שהסרטון לא יישאר סטטי לחלוטין", "en": " · a single change so the video is not completely static"},
    "pacing.single_change_note": {
        "he": "בסגנון הזה ובאורך הזה החישוב לא הניב אף שינוי ויזואלי, והסרטון היה יוצא סטטי לחלוטין. נקבע שינוי אחד בלבד, בקטע החזק ביותר ({role}).",
        "en": "With this style and length the calculation produced no visual change, and the video would have been completely static. A single change was set, in the strongest section ({role})."},
    "pacing.fast": {"he": "קצב מהיר", "en": "Fast pace"},
    "pacing.slow": {"he": "קצב איטי, עם אוויר", "en": "Slow pace, with air"},
    "pacing.normal": {"he": "קצב רגיל", "en": "Normal pace"},
    "pacing.why.hook": {"he": "הפתיחה חייבת לתפוס מיד", "en": "The opening has to grab attention immediately"},
    "pacing.why.emotional_peak": {"he": "השיא הרגשי מאבד מעוצמתו אם חותכים אותו", "en": "The emotional peak loses its power if it is cut"},
    "pacing.why.topic_change": {"he": "מעבר נושא מצדיק שינוי ויזואלי", "en": "A topic change justifies a visual change"},
    "pacing.why.setup": {"he": "רקע — אין צורך בהרבה תנועה", "en": "Background – not much movement needed"},
    "pacing.why.cta": {"he": "סיום ממוקד", "en": "A focused ending"},
    "pacing.why.filler": {"he": "קטע מילוי", "en": "Filler section"},
    "pacing.why.default": {"he": "גוף הסרטון", "en": "Body of the video"},
    "pacing.no_broll": {"he": " · ללא ויזואל חיצוני — הצופה רוצה את הדובר", "en": " · no outside visuals – the viewer wants the speaker"},
    "pacing.reason": {"he": "{pace}: {why}{tail}", "en": "{pace}: {why}{tail}"},

    # ---- וו פתיחה ----
    "hook.verdict.strong": {"he": "וו פתיחה חזק", "en": "Strong hook"},
    "hook.verdict.ok": {"he": "וו פתיחה סביר", "en": "Reasonable hook"},
    "hook.verdict.weak": {"he": "וו פתיחה חלש", "en": "Weak hook"},
    "hook.verdict.missing": {"he": "אין וו פתיחה", "en": "No hook"},
    "hook.empty_opening": {"he": "פתיחה בלי תוכן: „{text}”", "en": "Opening without content: “{text}”"},
    "hook.no_transcript": {"he": "אין תמלול, ולכן אי אפשר להעריך את הפתיחה.", "en": "There is no transcript, so the opening cannot be assessed."},
    "hook.needs_transcript": {"he": "הערכת הוו דורשת תמלול.", "en": "Assessing the hook requires a transcript."},
    "hook.trim": {"he": "גיזום {seconds} שניות מהפתיחה מקרב את הצופה לעניין.", "en": "Trimming {seconds} seconds from the opening gets the viewer to the point sooner."},
    "hook.trim_blocked": {
        "he": "הפתיחה מכילה מילות מילוי, אבל הן חלק מהמשפט הראשון בעל התוכן — גיזום היה חותך גם אותו.",
        "en": "The opening contains filler words, but they are part of the first sentence with content – trimming would cut it too."},
    "hook.move": {
        "he": "משפט זה מנקד {score} כוו פתיחה מול {current} של הפתיחה הנוכחית. העברה משנה את סדר הדברים שנאמרו, ולכן דורשת אישור.",
        "en": "This sentence scores {score} as a hook, against {current} for the current opening. Moving it changes the order of what was said, so it needs approval."},
    "hook.weak_no_move": {
        "he": "הפתיחה חלשה ולא נמצא בסרטון משפט חזק יותר להעביר לתחילתו.",
        "en": "The opening is weak and no stronger sentence was found in the video to move to the start."},
    "hook.explain.marker": {"he": "פותח בניסוח שמושך תשומת לב", "en": "Opens with attention-grabbing wording"},
    "hook.explain.curiosity": {"he": "מייצר שאלה בראש הצופה", "en": "Raises a question in the viewer's mind"},
    "hook.explain.specific": {"he": "קונקרטי ולא כללי", "en": "Concrete rather than general"},
    "hook.explain.good_length": {"he": "באורך טוב ({seconds} שניות)", "en": "A good length ({seconds} seconds)"},
    "hook.explain.too_long": {"he": "ארוך מדי לפתיחה ({seconds} שניות)", "en": "Too long for an opening ({seconds} seconds)"},
    "hook.explain.too_short": {"he": "קצר מכדי להיקלט", "en": "Too short to register"},
    "hook.explain.slow_start": {"he": "מתחיל בגרירת רגליים או במילות מילוי", "en": "Starts slowly or with filler words"},
    "hook.explain.neutral": {"he": "פתיחה ניטרלית — לא מזיקה, אבל גם לא מושכת", "en": "A neutral opening – not harmful, but not engaging either"},
}
