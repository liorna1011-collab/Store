"""מאסטרינג אודיו: יעדים, הסבר לכל שלב (מה הופעל ולמה) ואימות התוצאה."""

NAMESPACE = "mastering"

MESSAGES = {
    # ---- יעדים ----
    "target.social": {"he": "רשתות חברתיות", "en": "Social media"},
    "target.podcast": {"he": "פודקאסט", "en": "Podcast"},
    "target.broadcast": {"he": "שידור (EBU R128)", "en": "Broadcast (EBU R128)"},

    # ---- שמות שלבים ----
    "step.highpass": {"he": "סינון תדר נמוך", "en": "Low-cut filter"},
    "step.denoise": {"he": "הפחתת רעש", "en": "Noise reduction"},
    "step.gate": {"he": "שער רעש", "en": "Noise gate"},
    "step.level": {"he": "איזון עוצמות", "en": "Level balancing"},
    "step.compress": {"he": "דחיסה", "en": "Compression"},
    "step.normalize": {"he": "נרמול עוצמה", "en": "Loudness normalization"},
    "step.limit": {"he": "לימיטר", "en": "Limiter"},

    # ---- מדידה ----
    "measure.missing": {"he": "הקובץ לא נמצא.", "en": "The file was not found."},
    "measure.no_audio_detail": {"he": "אין פס קול לניתוח ({error}).", "en": "There is no soundtrack to analyze ({error})."},
    "measure.no_audio": {"he": "אין פס קול לניתוח.", "en": "There is no soundtrack to analyze."},
    "measure.read_failed": {"he": "קריאת הדגימות נכשלה ({error}).", "en": "Reading the samples failed ({error})."},
    "measure.empty": {"he": "פס הקול ריק.", "en": "The soundtrack is empty."},

    # ---- תכנית ----
    "plan.unmeasured": {"he": "לא ניתן היה למדוד את האודיו.", "en": "The audio could not be measured."},
    "plan.no_basis": {"he": "בלי מדידה אין בסיס להחלטה — לא בוצע עיבוד.",
                      "en": "Without a measurement there is no basis for a decision – no processing was done."},
    "plan.silent": {"he": "פס הקול שקט כמעט לחלוטין — אין מה למסטר.",
                    "en": "The soundtrack is almost completely silent – there is nothing to master."},
    "plan.clipped": {
        "he": "המקור כבר חתוך: {samples} דגימות בקצה הסקאלה ({percent}%). עיוות שנוצר בהקלטה אינו ניתן לביטול, וכל עיבוד כאן רק מסתיר אותו חלקית.",
        "en": "The source is already clipped: {samples} samples at full scale ({percent}%). Distortion created while recording cannot be undone, and any processing here only partly hides it."},
    "plan.noop": {
        "he": "המקור כבר עומד ביעד: עוצמה, טווח דינמי ורצפת רעש בתחום התקין. לא בוצע עיבוד — כל עיבוד כאן היה מוריד איכות.",
        "en": "The source already meets the target: loudness, dynamic range and noise floor are in range. No processing was done – any processing here would lower quality."},
    "plan.nothing_to_normalize": {"he": "אין מה לנרמל.", "en": "There is nothing to normalize."},

    # ---- סינון תדר נמוך ----
    "highpass.on": {
        "he": "{percent}% מהאנרגיה מתחת ל-100Hz — רעידות ורעש רצפה שאינם חלק מהקול.",
        "en": "{percent}% of the energy is below 100Hz – rumble and floor noise that are not part of the voice."},
    "highpass.off": {
        "he": "רק {percent}% מהאנרגיה בתדר נמוך — אין רעש רצפה שמצדיק סינון.",
        "en": "Only {percent}% of the energy is in the low band – no floor noise that justifies filtering."},

    # ---- הפחתת רעש ----
    "denoise.disabled": {"he": "הפחתת רעש כובתה בהגדרות.", "en": "Noise reduction is turned off in settings."},
    "denoise.after_highpass": {"he": " (אחרי סינון התדר הנמוך)", "en": " (after the low-cut filter)"},
    "denoise.no_pauses": {
        "he": "לא נמצאו מספיק הפסקות ({seconds} שנ') כדי למדוד את רעש הרקע. בלי מדידה לא מפעילים הפחתת רעש.",
        "en": "Not enough pauses were found ({seconds} s) to measure background noise. Without a measurement, noise reduction is not applied."},
    "denoise.clean": {
        "he": "הדיבור חזק מרעש הרקע ב-{snr}dB{basis} — הרעש לא נשמע. הפחתת רעש כאן רק הייתה מרככת את הקול.",
        "en": "Speech is {snr}dB louder than the background noise{basis} – the noise is inaudible. Noise reduction here would only soften the voice."},
    "denoise.on": {
        "he": "הדיבור חזק מרעש הרקע ב-{snr}dB בלבד{basis} — {severity}הפחתה של {reduction}dB, מוגבלת כדי לא לייבש את הקול.",
        "en": "Speech is only {snr}dB louder than the background noise{basis} – {severity}A reduction of {reduction}dB, capped so the voice does not dry out."},
    "denoise.strong": {"he": "רעש רקע נשמע בבירור. ", "en": "background noise is clearly audible. "},
    "denoise.light": {"he": "רעש רקע קל. ", "en": "light background noise. "},

    # ---- שער רעש ----
    "gate.unmeasured": {
        "he": "רעש הרקע לא נמדד — שער רעש בלי סף מדוד היה קוטע דיבור.",
        "en": "Background noise was not measured – a gate without a measured threshold would cut speech."},
    "gate.on": {
        "he": "{percent}% מהזמן הם הפסקות שבהן נשמע רק רעש הרקע. שער עדין משתיק אותן, עם שחרור איטי כדי לא לקטוע סופי מילים.",
        "en": "{percent}% of the time is pauses where only background noise is heard. A gentle gate silences them, with a slow release so word endings are not cut."},
    "gate.off": {
        "he": "אין צירוף של רעש רקע מורגש והפסקות ארוכות — שער רעש כאן היה קוטע סופי מילים בלי תועלת.",
        "en": "There is no combination of noticeable noise and long pauses – a gate here would cut word endings for no benefit."},

    # ---- איזון עוצמות ----
    "level.unmeasured": {"he": "לא נמדד טווח דינמי — לא משנים עוצמות בלי למדוד.",
                         "en": "Dynamic range was not measured – levels are not changed without measuring."},
    "level.even": {"he": "טווח דינמי {lra}LU — העוצמה כבר אחידה.", "en": "Dynamic range {lra}LU – the level is already even."},
    "level.on": {
        "he": "טווח דינמי {lra}LU — הפרש מורגש בין הקטעים השקטים לחזקים. איזון {strength}, בחלון ארוך כדי שלא יישמע „נושם”.",
        "en": "Dynamic range {lra}LU – a noticeable gap between quiet and loud parts. {strength} balancing over a long window so it does not sound like “breathing”."},
    "level.strong": {"he": "חזק", "en": "Strong"},
    "level.moderate": {"he": "מתון", "en": "Moderate"},

    # ---- דחיסה ----
    "compress.disabled": {"he": "דחיסה כובתה בהגדרות.", "en": "Compression is turned off in settings."},
    "compress.unmeasured": {"he": "לא נמדד יחס שיא/RMS.", "en": "The peak/RMS ratio was not measured."},
    "compress.dense": {
        "he": "הפרש שיא/RMS {crest}dB — הקול כבר צפוף מספיק. דחיסה נוספת רק הייתה שוטחת אותו.",
        "en": "Peak/RMS gap {crest}dB – the voice is already dense enough. More compression would only flatten it."},
    "compress.on": {
        "he": "הפרש שיא/RMS {crest}dB — פסגות בודדות גבוהות בהרבה מהדיבור. דחיסה קלה מקרבת ביניהן.",
        "en": "Peak/RMS gap {crest}dB – single peaks are much louder than the speech. Light compression brings them closer."},

    # ---- נרמול ----
    "normalize.unmeasured": {"he": "לא נמדדה עוצמה משולבת — לא מנרמלים באפלה.",
                             "en": "Integrated loudness was not measured – no normalizing in the dark."},
    "normalize.close": {
        "he": "העוצמה {lufs}LUFS, היעד {target} — הפרש של {delta}LU שלא נשמע. לא נוגעים.",
        "en": "Loudness {lufs}LUFS, target {target} – a {delta}LU gap that is inaudible. Left untouched."},
    "normalize.gain_limited": {
        "he": "העוצמה {lufs}LUFS מול יעד {target} ({label}). מתחת לתקרת השיא נכנסים רק {headroom}dB הגבר, ולכן ההגבר המלא ({delta}dB) מלווה בלימיטר שתופס את הפסגות — הגבר לינארי לבדו לא היה מגיע ליעד.",
        "en": "Loudness {lufs}LUFS against a target of {target} ({label}). Only {headroom}dB of gain fits under the peak ceiling, so the full gain ({delta}dB) comes with a limiter that catches the peaks – linear gain alone would not reach the target."},
    "normalize.linear": {
        "he": "העוצמה {lufs}LUFS מול יעד {target} ({label}) — הקול {direction} ב-{delta}LU בהגבר לינארי, בלי לשנות את הדינמיקה.",
        "en": "Loudness {lufs}LUFS against a target of {target} ({label}) – the voice is {direction} by {delta}LU with linear gain, without changing the dynamics."},
    "normalize.up": {"he": "מוגבר", "en": "raised"},
    "normalize.down": {"he": "מונמך", "en": "lowered"},

    # ---- לימיטר ----
    "limit.after_gain": {
        "he": "ההגבר מוציא את השיא מעל {ceiling}dBTP — הלימיטר תופס את הפסגות ומונע עיוות.",
        "en": "The gain pushes the peak above {ceiling}dBTP – the limiter catches the peaks and prevents distortion."},
    "limit.normalized": {
        "he": "הנרמול כבר מגביל את השיא ל-{ceiling}dBTP — לימיטר נוסף היה חותך פעמיים.",
        "en": "Normalization already limits the peak to {ceiling}dBTP – another limiter would cut twice."},
    "limit.below": {
        "he": "השיא {peak} — מתחת לתקרה של {ceiling}dBTP.",
        "en": "The peak is {peak} – below the {ceiling}dBTP ceiling."},
    "limit.peak_unmeasured": {"he": "לא נמדד", "en": "not measured"},
    "limit.on": {
        "he": "השיא {peak}dBTP חורג מהתקרה {ceiling}dBTP — לימיטר מונע עיוות בהמרה לפורמט דחוס.",
        "en": "The peak of {peak}dBTP exceeds the {ceiling}dBTP ceiling – a limiter prevents distortion when converting to a compressed format."},

    # ---- תוצאה ----
    "result.noop": {"he": "לא בוצע עיבוד: המקור כבר עומד ביעד.", "en": "No processing: the source already meets the target."},
    "result.summary": {"he": "{steps} · {before} ← {after} LUFS (יעד {target})",
                       "en": "{steps} · {before} → {after} LUFS (target {target})"},
    "result.reached": {
        "he": "אחרי העיבוד העוצמה הגיעה ל-{lufs}LUFS מעצמה, ולכן לא הופעל הגבר נוסף.",
        "en": "After processing, loudness reached {lufs}LUFS on its own, so no extra gain was applied."},
    "result.second_pass": {
        "he": "שלב שני: אחרי העיבוד נמדדו {lufs}LUFS, וההגבר חושב מהמדידה הזו ולא מהמקורית.",
        "en": "Second pass: {lufs}LUFS was measured after processing, and the gain was computed from that measurement, not the original one."},
    "result.timeout": {"he": "עיבוד האודיו חרג מזמן ההמתנה.", "en": "Audio processing exceeded the time limit."},
    "result.ffmpeg_failed": {"he": "FFmpeg נכשל: {detail}", "en": "FFmpeg failed: {detail}"},
    "verify.unmeasured": {"he": "לא ניתן היה למדוד את התוצאה.", "en": "The result could not be measured."},
    "verify.silent": {"he": "התוצאה שקטה — משהו בעיבוד מחק את הקול.", "en": "The result is silent – something in processing erased the audio."},
    "verify.no_lufs": {"he": "העוצמה בתוצאה לא נמדדה.", "en": "The loudness of the result was not measured."},
    "verify.drift": {
        "he": "העוצמה אחרי העיבוד {lufs}LUFS, היעד {target} — פער של {drift}LU.",
        "en": "Loudness after processing is {lufs}LUFS, the target is {target} – a gap of {drift}LU."},
    "verify.peak": {
        "he": "השיא בתוצאה {peak}dBTP חורג מהתקרה {ceiling}dBTP.",
        "en": "The peak in the result, {peak}dBTP, exceeds the {ceiling}dBTP ceiling."},
    "verify.clipping": {
        "he": "העיבוד הוסיף קליפינג: {after}% מהדגימות, לעומת {before}% במקור.",
        "en": "Processing added clipping: {after}% of samples, compared with {before}% in the source."},
}
