# Gold references – schema and metrics

A gold reference is a human editor's map of one source video: which moments
should become Shorts, which are maybes, which must never be chosen, which
topics make long-form videos, and which words a correct subtitle must contain.
Polixor's acceptance kit scores every run against it. The clip engine never
grades its own output.

Files:

| Where | What | In git |
|---|---|---|
| `gold/local/<id>.gold.json` | readable gold, with the real words | no (gitignored) |
| `gold/<id>.gold.json` | the same file with every text replaced by salted per-token hashes (`scripts/gold_eval.py redact`) | yes |

Both forms score identically: a phrase is found when the hashes of the
subtitle tokens match in order. A Hebrew prefix letter (ו, ה, ב, כ, ל, מ, ש)
may be attached to a token.

## Schema `polixor.gold/1`

```jsonc
{
 "schema": "polixor.gold/1",
 "id": "news_liberman",                 // also the hash salt unless "salt" is given
 "status": "engineering_reference",     // | "verified" | "blind_ratings" | "draft_unreviewed"
 "coverage": "full",                    // full: a clip that matches nothing is not shippable
                                        // partial: such a clip is "unrated" (left out of precision)
 "source": {"duration": 720.67, "language": "he",
            "match": {"name_contains": ["liberman"]}},   // auto-detection by file name (+ length ±3 s)
 "moments": [{
   "id": "N13", "start": 670.7, "end": 717.7,
   "label": "ship",                     // ship | maybe | hold | no
   "score": 10, "kind": "question_answer", "topic": "L4",
   "anchors": [                         // the lines a correct clip must contain
     {"role": "hook",   "t": 670.69, "t_end": 671.3, "text": "…"},
     {"role": "payoff", "t": 716.79, "t_end": 717.6, "text": "…"}],
   "cuts": [[686.9, 689.3]],            // internal cuts an editor would make
   "unresolved": ["U2"]}],
 "negatives": [{"id": "X1", "start": 0, "end": 11, "reason": "greetings"}],
 "topics": [{"id": "L1", "start": 0, "end": 283, "moments": ["N1", "N2"], "remove": [[203, 228]]}],
 "terms": [{"kind": "name", "text": "…", "accept": ["other spelling"], "t": [485.5],
            "status": "verified"}],    // "unresolved": counted, never scored
 "unresolved": [{"id": "U2", "t": 150.97, "kind": "name", "alternatives": ["…", "…"]}]
}
```

Uncertainty is first-class: a word, number or speaker nobody verified by ear
is listed under `unresolved` (or as a term with `status: "unresolved"`). It is
reported as "unresolved inside clips" and never counted as right or wrong.

## Matching

- A clip **is** a gold moment when it contains the moment's hook and payoff
  anchors (±0.5 s) and at least half of the clip lies inside the moment.
  Without anchors (ratings of earlier clips) it must cover 60% of the moment.
  When several moments match, the one with the highest overlap wins.
- The **pool has** a moment when some candidate covers 40% of it and its payoff.

## Metrics

| Metric | Meaning |
|---|---|
| precision | credit per rated clip: ship = 1, maybe = ½, everything else 0 |
| recall@ship | ship moments matched by a shipped Short (zero outputs = 0) |
| recall@pool | ship moments present in the candidate pool before the final cut |
| negative hits | Shorts lying mostly (≥50%) on a must-not-choose region |
| boundary error | mean |start − gold start| and |end − gold end| of matched clips; clips that cut the payoff |
| transcript | key phrases (anchors) present in the final subtitles of the clips that contain them |
| names / numbers | verified terms present in the final subtitles |
| hook grounding | on-screen hooks whose content words all occur in the clip's subtitles (numbers exactly) |
| duplicate rate | pairs of Shorts with IoU ≥ 0.3 or matching the same gold moment, per Short |
| source coverage | share of the source heard by the strong ASR model and read by the semantic layer |
| mode visibility | the run declared semantic or degraded mode, and a degraded run labelled its clips |
| long-form | gold topics covered (≥60%, ≤40% outside) by a long-form video |

## Rules

- Gold files of the blind test sources (the third unseen source and the final
  3–4 h source) are written **after** their blind evaluation, never before.
  Nothing is tuned on them.
- A `draft_unreviewed` map (for example the full-source draft of a livestream)
  is never used for scoring until a person reviews it and changes its status.
