"""
Prompts and JSON schemas of the semantic pipeline.

Rules shared by every prompt: read only the transcript given; cite sentence
IDs and exact quotes; never invent words, names, numbers or speakers; say so
when something is unclear. The model answers in JSON that matches the schema;
the code validates every ID, quote and time afterwards (validate.py).
"""

from __future__ import annotations

from typing import Any

from .validate import TYPES

COMMON = """You are the senior editor of a team that turns long Hebrew (sometimes English) videos – \
interviews, debates, podcasts, livestreams – into short vertical clips (Shorts/Reels/TikTok) and \
long-form topic videos.

The transcript is given one sentence per line:
  [s0042 291.3-294.1 t6] sentence text
s0042 is the sentence ID, then start-end seconds, then a turn number (a new turn starts after a \
pause or after a question; turns are NOT speaker identities). Flags such as "loop" or "fast_asr" \
mark sentences whose words are less reliable.

Non-negotiable rules:
1. Use only what is in the transcript. Never invent words, names, numbers, events or facts.
2. Every quote you give must be copied exactly, word for word, from the sentence you cite.
3. Cite sentence IDs exactly as written. Times must come from those sentences.
4. Never say who spoke a line (a name, "the host", "the guest") unless the line itself says it. \
The transcript has no speaker identification.
5. Speech recognition makes mistakes. If a key word looks garbled, say so in your notes instead \
of guessing what it should be.
6. Write titles, summaries and hooks in the language of the transcript."""

TYPE_GUIDE = """Moment types and their evidence roles (cite them in this order):
- question_answer: question → answer (the answer must actually answer)
- claim_explanation: claim → explanation
- accusation_response: accusation → response
- disagreement: disagreement → argument (optional) → conclusion
- setup_payoff: setup → payoff
- opinion_evidence_verdict: opinion → evidence (optional) → verdict
- story: story → turn (optional) → conclusion
- strong_quote: quote (a line strong enough to stand alone, with the context it needs)
- emotional / surprising / useful: moment (a genuinely emotional, surprising or useful moment)"""

RUBRIC = """Score each criterion 0, 1 or 2:
- hook: do the first seconds make a viewer stay? (a sharp question, a provocative claim, a conflict)
- clarity: can a viewer who never saw the video understand it without more context?
- payoff: does it end on an answer, a verdict, a punchline, a resolution – not mid-thought?
- interest: stakes, surprise, emotion, usefulness, or a memorable line
- feasibility: can it be subtitled and cut cleanly? (crosstalk, garbled words, cut-off sentences lower it)"""


def topic_map_prompt(chunk_text: str, context_text: str, language: str) -> tuple[str, str]:
    system = COMMON + """

Task: map the TOPICS of one part of the video. A topic is a stretch where the speakers discuss \
one subject or one question (with its answers, arguments and conclusion). Also mark JUNK – \
stretches with no value for viewers: greetings, sign-offs, logistics, technical problems, \
dead air, reading chat names, ads, tangents that go nowhere, unintelligible crosstalk."""
    user = (f"Language: {language or 'unknown'}.\n\n"
            + (f"CONTEXT (already mapped – do not map it again, use it only to know whether the first "
               f"topic below continues it):\n{context_text}\n\n" if context_text else "")
            + f"TRANSCRIPT TO MAP:\n{chunk_text}\n\n"
            "Return every topic in the transcript to map, in order, without overlaps. For each: start_id "
            "and end_id (inclusive), title (short, specific), summary (1–2 sentences), central (the "
            "central question or claim), continues_previous (true if the first topic continues the "
            "context topic), short_potential and long_form_value (high/medium/low/none). Then the junk "
            "ranges with their kind. Finally list the proper names discussed (people, parties, places, "
            "organisations, brands): the correct spelling, and heard_as – the exact words the transcript wrote "
            "for it (speech recognition often misspells names).")
    return system, user


TOPIC_MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "start_id": {"type": "string"}, "end_id": {"type": "string"},
                "title": {"type": "string"}, "summary": {"type": "string"}, "central": {"type": "string"},
                "continues_previous": {"type": "boolean"},
                "short_potential": {"type": "string", "enum": ["high", "medium", "low", "none"]},
                "long_form_value": {"type": "string", "enum": ["high", "medium", "low", "none"]},
            },
            "required": ["start_id", "end_id", "title", "summary", "central", "continues_previous",
                         "short_potential", "long_form_value"],
            "additionalProperties": False}},
        "junk": {"type": "array", "items": {
            "type": "object",
            "properties": {"start_id": {"type": "string"}, "end_id": {"type": "string"},
                           "kind": {"type": "string", "enum": ["greeting", "signoff", "logistics", "technical",
                                                               "dead_air", "chat_reading", "ad", "tangent",
                                                               "crosstalk", "unintelligible"]}},
            "required": ["start_id", "end_id", "kind"], "additionalProperties": False}},
        "names": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "heard_as": {"type": "string"}},
            "required": ["name", "heard_as"], "additionalProperties": False}},
    },
    "required": ["topics", "junk", "names"], "additionalProperties": False,
}


def merge_topics_prompt(listing: str) -> tuple[str, str]:
    system = COMMON + """

Task: build the GLOBAL topic map of the whole video from per-part maps. Parts were mapped \
separately, so one subject may be split in two at a part boundary, and a subject may come back \
later. Merge pieces that are the same subject. Keep different subjects apart."""
    user = ("Topic pieces in time order (id, time range, title, summary):\n" + listing + "\n\n"
            "Return the global topics. Each lists the piece ids it is made of (every piece exactly once, "
            "pieces of one topic in time order), a title, a summary and long_form_value.")
    return system, user


MERGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"topics": {"type": "array", "items": {
        "type": "object",
        "properties": {"pieces": {"type": "array", "items": {"type": "string"}},
                       "title": {"type": "string"}, "summary": {"type": "string"},
                       "long_form_value": {"type": "string", "enum": ["high", "medium", "low", "none"]}},
        "required": ["pieces", "title", "summary", "long_form_value"], "additionalProperties": False}}},
    "required": ["topics"], "additionalProperties": False,
}


def candidates_prompt(topic_title: str, topic_summary: str, text: str, language: str,
                      min_s: float, max_s: float) -> tuple[str, str]:
    system = COMMON + "\n\n" + TYPE_GUIDE + "\n\n" + RUBRIC + f"""

Task: find EVERY moment in this topic that could become a strong Short. Quiet moments count as \
much as loud ones: a sharp answer, a clean argument, a memorable line. A Short must stand alone: \
it starts where a viewer can follow (with the question or claim, not mid-thought) and ends on \
its payoff. Shorts usually run {int(min_s)}–{int(max_s)} seconds, but a complete moment that \
needs more or less time is better than a broken one – duration is a preference, not a rule. \
Do not propose greetings, logistics, chat reading or moments whose payoff is missing."""
    user = (f"Language: {language or 'unknown'}.\nTopic: {topic_title}\nSummary: {topic_summary}\n\n"
            f"TRANSCRIPT (lines marked » are context outside the topic):\n{text}\n\n"
            "For each moment: type, start_id, end_id, evidence (role, sentence_id, exact quote – in role "
            "order), the rubric scores with one short reason each, standalone (what a viewer would be "
            "missing, empty if nothing), cut_ids (sentence ids inside the span that an editor would cut: "
            "interruptions, crosstalk, a tangent – never the evidence), and a working title. "
            "Include weaker moments too (they compete later), but never one without its closing evidence.")
    return system, user


def _rubric_schema() -> dict[str, Any]:
    crit = {"type": "object", "properties": {"score": {"type": "integer", "enum": [0, 1, 2]},
                                             "reason": {"type": "string"}},
            "required": ["score", "reason"], "additionalProperties": False}
    return {"type": "object",
            "properties": {k: crit for k in ("hook", "clarity", "payoff", "interest", "feasibility")},
            "required": ["hook", "clarity", "payoff", "interest", "feasibility"], "additionalProperties": False}


CANDIDATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"moments": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(TYPES)},
            "start_id": {"type": "string"}, "end_id": {"type": "string"},
            "evidence": {"type": "array", "items": {
                "type": "object",
                "properties": {"role": {"type": "string"}, "sentence_id": {"type": "string"},
                               "quote": {"type": "string"}},
                "required": ["role", "sentence_id", "quote"], "additionalProperties": False}},
            "rubric": _rubric_schema(),
            "standalone": {"type": "string"},
            "cut_ids": {"type": "array", "items": {"type": "string"}},
            "title": {"type": "string"},
        },
        "required": ["type", "start_id", "end_id", "evidence", "rubric", "standalone", "cut_ids", "title"],
        "additionalProperties": False}}},
    "required": ["moments"], "additionalProperties": False,
}


def rank_prompt(items: str) -> tuple[str, str]:
    system = COMMON + "\n\n" + RUBRIC + """

Task: you are judging candidate Shorts from different parts of the same video against each other. \
Judge each on what a viewer would actually see: does it hook, is it understandable alone, does it \
pay off, is it worth watching? Where in the video a candidate comes from does not matter. \
Be strict: "ship" means you would publish it as is (after normal subtitling)."""
    user = ("Candidates (key, duration, transcript of the clip):\n" + items + "\n\n"
            "Rank ALL of them from best to worst (every key exactly once), and give each a verdict "
            "(ship / maybe / no) with a one-line reason.")
    return system, user


RANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ranking": {"type": "array", "items": {"type": "string"}},
        "verdicts": {"type": "array", "items": {
            "type": "object",
            "properties": {"key": {"type": "string"},
                           "verdict": {"type": "string", "enum": ["ship", "maybe", "no"]},
                           "reason": {"type": "string"}},
            "required": ["key", "verdict", "reason"], "additionalProperties": False}},
    },
    "required": ["ranking", "verdicts"], "additionalProperties": False,
}


def boundary_prompt(text: str, starts: str, ends: str, evidence: str, min_s: float, max_s: float) -> tuple[str, str]:
    system = COMMON + f"""

Task: choose the best cut of one Short. You get the surrounding transcript, the allowed start \
sentences and the allowed end sentences. A good start lets a viewer follow from the first word \
(the question or the claim – not a dangling "and so", not a greeting); a good end lands on the \
payoff (the answer, the verdict, the punchline) and may keep a short reaction, but does not \
trail into the next subject. Inside the clip you may cut whole sentences that hurt it \
(interruptions, crosstalk, a tangent, a repeated start) – never the evidence. Shorts usually \
run {int(min_s)}–{int(max_s)} seconds; completeness matters more than length."""
    user = (f"TRANSCRIPT:\n{text}\n\nEVIDENCE (must stay in the clip):\n{evidence}\n\n"
            f"ALLOWED STARTS:\n{starts}\n\nALLOWED ENDS:\n{ends}\n\n"
            "Return start_id, end_id, cut_ids (may be empty) and a one-line reason for each choice.")
    return system, user


BOUNDARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"start_id": {"type": "string"}, "end_id": {"type": "string"},
                   "cut_ids": {"type": "array", "items": {"type": "string"}},
                   "start_reason": {"type": "string"}, "end_reason": {"type": "string"},
                   "cut_reason": {"type": "string"}},
    "required": ["start_id", "end_id", "cut_ids", "start_reason", "end_reason", "cut_reason"],
    "additionalProperties": False,
}


def reconstruct_prompt(text: str, starts: str, ends: str, evidence: str, current: str, critique: str,
                       min_s: float, max_s: float) -> tuple[str, str]:
    system = COMMON + f"""

Task: rebuild the cut of a Short whose MOMENT is strong but whose first cut failed the final \
editor. You get the editor's critique, the current cut, a wider transcript window and the \
allowed starts and ends. Fix what the editor named: start where a stranger can follow (bring in \
the question or the context the clip depends on, or drop setup that delays the point), end on \
the payoff (the answer, the verdict, the punchline – extend to it if the first cut stopped \
early) and not after it, and cut whole sentences inside that hurt the clip (dead stretches, \
interruptions, a tangent, a repeated start) – never the evidence that carries the point. Keep \
it as short as the story allows: Shorts usually run {int(min_s)}–{int(max_s)} seconds; \
completeness matters more than length. If no cut in the allowed range can fix it, say so \
(fixable=false) – an honest "cannot" is better than a cut that only moves the problem."""
    user = (f"EDITOR'S CRITIQUE:\n{critique}\n\nCURRENT CUT: {current}\n\nTRANSCRIPT:\n{text}\n\n"
            f"EVIDENCE (the moment itself):\n{evidence}\n\nALLOWED STARTS:\n{starts}\n\nALLOWED ENDS:\n{ends}\n\n"
            "Return fixable, start_id, end_id, cut_ids (may be empty) and a one-line reason for each choice.")
    return system, user


RECONSTRUCT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"fixable": {"type": "boolean"}, "start_id": {"type": "string"}, "end_id": {"type": "string"},
                   "cut_ids": {"type": "array", "items": {"type": "string"}},
                   "start_reason": {"type": "string"}, "end_reason": {"type": "string"},
                   "cut_reason": {"type": "string"}},
    "required": ["fixable", "start_id", "end_id", "cut_ids", "start_reason", "end_reason", "cut_reason"],
    "additionalProperties": False,
}


def adjudicate_prompt(items: str) -> tuple[str, str]:
    system = COMMON + """

Task: speech recognition heard some words of a clip differently in independent passes. For each \
disagreement you get the sentence and the alternatives that were actually heard. Choose the \
alternative that fits the sentence, the topic and Hebrew grammar best – or "unresolved" when you \
cannot tell. You may ONLY choose one of the listed alternatives: never write a word nobody heard."""
    user = ("Disagreements (id, sentence with the disputed words in ⟦ ⟧, alternatives):\n" + items + "\n\n"
            "Return a decision for every id.")
    return system, user


ADJUDICATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "choice": {"type": "string"},
                       "unresolved": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["id", "choice", "unresolved", "reason"], "additionalProperties": False}}},
    "required": ["decisions"], "additionalProperties": False,
}


def hooks_prompt(text: str, kind: str, verified_terms: str, language: str) -> tuple[str, str]:
    system = COMMON + """

Task: write the on-screen EDITORIAL HOOK of one Short – the 3 to 9 words shown over the first \
seconds so a viewer scrolling past stays. It is not the first spoken line (that is the content \
hook); it frames what the viewer is about to see. It must be true to the clip: everything it \
claims is said in the clip. No clickbait, no promises the clip does not keep, no "you won't \
believe", no emojis. Use a name or a number only if it appears in the VERIFIED list. Natural, \
idiomatic language – the way a good editor in that language would write it.
Also write title options for the post."""
    user = (f"Language: {language or 'unknown'}. Moment type: {kind}.\n"
            f"VERIFIED names and numbers (you may use only these): {verified_terms or '(none)'}\n\n"
            f"CLIP TRANSCRIPT:\n{text}\n\n"
            "Return 5 different hook candidates, each with the exact quote(s) from the clip that support "
            "it and self-scores (1–5) for truthfulness, specificity, curiosity, clarity, natural language "
            "and relevance; then 3 title options.")
    return system, user


HOOKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hooks": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "support": {"type": "array", "items": {"type": "string"}},
                "scores": {"type": "object", "properties": {
                    k: {"type": "integer", "enum": [1, 2, 3, 4, 5]}
                    for k in ("truthfulness", "specificity", "curiosity", "clarity", "natural", "relevance")},
                    "required": ["truthfulness", "specificity", "curiosity", "clarity", "natural", "relevance"],
                    "additionalProperties": False}},
            "required": ["text", "support", "scores"], "additionalProperties": False}},
        "titles": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hooks", "titles"], "additionalProperties": False,
}


CHECKS = ("opening_hooks", "standalone", "payoff", "clean_ending", "pacing")


def editor_prompt(product: str) -> tuple[str, str]:
    system = COMMON + "\n\n" + RUBRIC + """

Task: final editor check of one finished Short before it is delivered. You see the complete \
product: the cut (with times and internal cuts), the final subtitle text with uncertain words \
marked ⟦?⟧, what the viewer hears first, the title, pacing numbers and the framing. There is \
NO text overlay on the video: the content itself has to hook.

The standard: would a senior social-video editor deliver this to a paying client today? \
Answer each check honestly (true only if it clearly holds):
- opening_hooks: the first seconds of speech give a reason to keep watching (not a greeting, \
  not a dangling "so…", not setup the viewer cannot place)
- standalone: a stranger who never saw the source understands who/what it is about
- payoff: the clip delivers what it sets up – the answer, the verdict, the punchline, the \
  reaction, the turn of the story. A clip that stops before its point fails this.
- clean_ending: it ends on a finished thought, not mid-sentence and not trailing into the next subject
- pacing: no dead stretch, no repeated restart, nothing a viewer would skip

Verdict: "ship" only when every check holds. "repair" when a listed fix would make every check \
hold – name the fixes. "reject" when it cannot (a missing payoff that is not in the allowed \
range, a moment that is only interesting with context the clip cannot carry, a weak moment). \
Rejecting is a normal, good outcome: only excellent clips are delivered.
Possible fixes: better_start (a start sentence id from the allowed list), better_end (an end \
sentence id from the allowed list), new_hook (the title is weak or untrue), rehear (sentence ids \
whose words must be checked against the audio), tighten (sentence ids to cut whole sentences \
such as crosstalk or a tangent – never the evidence)."""
    user = product + "\n\nReturn verdict, checks, scores, fixes (may be empty) and a short reason."
    return system, user


EDITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ship", "repair", "reject"]},
        "checks": {"type": "object", "properties": {k: {"type": "boolean"} for k in CHECKS},
                   "required": list(CHECKS), "additionalProperties": False},
        "scores": _rubric_schema(),
        "fixes": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["better_start", "better_end", "new_hook",
                                                               "rehear", "tighten"]},
                           "sentence_ids": {"type": "array", "items": {"type": "string"}},
                           "note": {"type": "string"}},
            "required": ["kind", "sentence_ids", "note"], "additionalProperties": False}},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "checks", "scores", "fixes", "reason"], "additionalProperties": False,
}


def longform_prompt(text: str, title: str, min_s: float, target_s: float) -> tuple[str, str]:
    system = COMMON + f"""

Task: edit one topic of the video into a clean long-form video. Keep the structure that makes it \
worth watching: every question with its answer, every argument with its response, stories with \
their turn and conclusion, and the conclusion of the topic. Remove what wastes the viewer's \
time when it is safe: greetings, logistics, technical talk, chat reading, dead air, tangents, \
unintelligible crosstalk, false starts. Never remove part of a question/answer pair. \
Build it like an editor builds a topic video: an OPENING that tells the viewer why to watch \
(the strongest framing of the topic – it may be the moment that states the question), the \
CONTEXT a stranger needs, the DEVELOPMENT, the strongest sections, and the CONCLUSION or payoff. \
Ranges stay in source order; do not stitch unrelated chunks together. \
Target about {int(target_s // 60)} minutes or less; at least {int(min_s)} seconds."""
    user = (f"Topic: {title}\n\nTRANSCRIPT:\n{text}\n\n"
            "Return the sentence ranges to KEEP in order (start_id, end_id, purpose), a title and a "
            "one-line description.")
    return system, user


LONGFORM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "keep": {"type": "array", "items": {
            "type": "object",
            "properties": {"start_id": {"type": "string"}, "end_id": {"type": "string"},
                           "purpose": {"type": "string"}},
            "required": ["start_id", "end_id", "purpose"], "additionalProperties": False}},
        "title": {"type": "string"}, "description": {"type": "string"},
    },
    "required": ["keep", "title", "description"], "additionalProperties": False,
}


LONGFORM_CHECKS = ("opening", "context", "development", "payoff", "coherent")


def longform_review_prompt(outline: str, title: str, seconds: float) -> tuple[str, str]:
    system = COMMON + """

Task: final editor check of one long-form topic video before it is delivered. You get the kept \
parts in order (with what each part is for) and their text. Answer each check honestly:
- opening: the first minute tells a viewer what this is about and why to keep watching
- context: a stranger can follow (who / what / why is set up before it is needed)
- development: it builds – each part follows from the previous, no random jumps between subjects
- payoff: it reaches a conclusion, an answer or a turn – it does not just stop
- coherent: it is ONE topic, not unrelated chunks stitched together
Verdict "ship" only when every check holds; otherwise "reject" with the reason. A rejected \
topic video is a normal outcome: only coherent videos are delivered."""
    user = f"TITLE: {title}\nLENGTH: {seconds / 60:.1f} min\n\nPARTS:\n{outline}\n\nReturn verdict, checks and a short reason."
    return system, user


LONGFORM_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ship", "reject"]},
        "checks": {"type": "object", "properties": {k: {"type": "boolean"} for k in LONGFORM_CHECKS},
                   "required": list(LONGFORM_CHECKS), "additionalProperties": False},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "checks", "reason"], "additionalProperties": False,
}
