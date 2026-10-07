"""
Runs the semantic pipeline end to end for one job (see __init__.py).

    run(inputs) -> Outcome

When no semantic model is usable the outcome is mode="degraded" with the
reason, and the caller runs the old engine with that label. Everything is
checkpointed under <work_dir>/intel/ and every finished Short is checkpointed
separately, so a crash during the eighth Short resumes at the eighth.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ...util import profiler
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

from ... import i18n
from ...config import AppSettings
from .. import asr_ensemble
from ..transcribe import TranscriptResult
from . import boundaries, editor, hooks, longform_plan, profile, ranking
from . import sentences as S
from .candidates import Cand, discover
from .checkpoint import StageStore, key_of
from .provider import SemanticError, SemanticProvider, resolve
from .topics import TopicMap, build_topic_map

log = logging.getLogger("polixor.semantic.run")

RESERVES = 4                   # minimum extra candidates that replace rejected Shorts (ranking.edit_budget)
SALVAGE = 3                    # when nothing ships: further strong candidates tried before giving up
NEAR_PASS_SHOWN = 2            # good moments whose cut is still imperfect: delivered as NEEDS REVIEW (never "ready")
SHORTS_PARALLEL = 3            # Shorts prepared at the same time (model latency overlaps)
CATEGORY = {"question_answer": "story", "claim_explanation": "story", "accusation_response": "argument",
            "disagreement": "argument", "setup_payoff": "funny", "opinion_evidence_verdict": "argument",
            "story": "story", "strong_quote": "highlight", "emotional": "moment", "surprising": "surprise",
            "useful": "highlight"}


@dataclass
class Inputs:
    transcript: Optional[TranscriptResult]
    settings: AppSettings
    work_dir: Path
    language: str = ""
    duration: float = 0.0
    audio_path: Optional[Path] = None
    limit: int = 5
    want_longform: bool = False
    cancel_event: Optional[threading.Event] = None
    note: Callable[[str], None] = lambda _t: None
    progress: Callable[[float, str], None] = lambda _f, _m: None
    provider: Optional[SemanticProvider] = None         # tests / replays
    engines: Optional[dict[str, Any]] = None            # tests: strong / second / rehear callables
    vocabulary: Sequence[str] = ()
    discovery_strong: bool = True
    profile: dict[str, Any] = field(default_factory=dict)   # filled by run(): the content profile used
    on_ship: Optional[Callable[[Any, dict], None]] = None    # (candidate, final words): render it now
    # streaming: Shorts an early window already shipped and rendered ({"cand": selection.Candidate,
    # "final": words, "key", "window"}) – they count toward the limit and their moments are taken
    preshipped: list[dict[str, Any]] = field(default_factory=list)
    horizon: Optional[float] = None                      # an early window: candidates must end before this
    on_candidates: Optional[Callable[[int], None]] = None    # the pool is known (milestone)


@dataclass
class Outcome:
    mode: str                                            # semantic | degraded
    reason: str = ""
    shorts: list[Any] = field(default_factory=list)      # selection.Candidate
    longforms: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    final: dict[str, Any] = field(default_factory=dict)  # asr_ensemble file data
    sentences: list[S.Sentence] = field(default_factory=list)
    topic_map: Optional[TopicMap] = None
    near_pass: list[Any] = field(default_factory=list)   # shown "needs attention" only when nothing ships
    unreviewed: int = 0                                   # clips the editor could not judge (infrastructure)


class _Timer:
    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}

    @contextmanager
    def __call__(self, name: str) -> Iterator[None]:
        t0, c0 = time.time(), time.thread_time()
        try:
            yield
        finally:
            self.seconds[name] = round(self.seconds.get(name, 0.0) + time.time() - t0, 2)
            p = profiler.current()
            if p is not None:
                p.span(f"semantic.{name}", time.time() - t0, time.thread_time() - c0)


def _engines(inp: Inputs) -> dict[str, Any]:
    if inp.engines is not None:
        return inp.engines
    if inp.audio_path is None or not Path(inp.audio_path).exists():
        return {}
    tr = inp.transcript
    if tr is None or tr.provider != "faster-whisper" or inp.settings.transcript_provider != "faster-whisper":
        return {}                     # no local speech model in this run (tests, fixtures, no transcription)
    from .. import asr_engines
    from ...config import SECRETS
    from ...profiles import asr_plan
    from ..vocabulary import hotwords

    plan = asr_plan(inp.settings, language=inp.language or None)
    hw = hotwords(list(inp.settings.asr_vocabulary) + list(inp.vocabulary)) or None
    strong = asr_engines.LocalEngine(inp.audio_path, inp.settings, inp.language or None, model=plan.strong_model,
                                     label="A", beam=max(5, plan.beam_size), hotwords=hw,
                                     cancel_event=inp.cancel_event)
    if getattr(inp.settings, "asr_cloud_fallback", False) and SECRETS.get("openai_api_key"):
        second: Any = asr_engines.CloudEngine(inp.audio_path, inp.settings, inp.language or None, inp.cancel_event)
        second_label = f"cloud:{second.model_name}"
    else:
        model = asr_engines.second_model(plan.strong_model, inp.language or None)
        second = asr_engines.LocalEngine(inp.audio_path, inp.settings, inp.language or None, model=model,
                                         label="B", beam=5, hotwords=hw, cancel_event=inp.cancel_event)
        second_label = f"local:{model}"
    return {"strong": strong, "second": second, "rehear": strong,
            "labels": {"A": f"strong:{plan.strong_model}", "B": second_label, "C": "discovery",
                       "D": f"re-heard:{plan.strong_model}"}}


def run(inp: Inputs) -> Outcome:
    timer = _Timer()
    store = StageStore(inp.work_dir)
    provider, why = (inp.provider, "") if inp.provider is not None else \
        resolve(inp.settings, cache_dir=Path(inp.work_dir) / "intel" / "llm")
    if inp.transcript is None or not inp.transcript.has_speech:
        return Outcome("degraded", "no_transcript")
    fp = S.fingerprint(inp.transcript)
    with timer("sentences"):
        cached = store.get("sentences", key_of("sentences", fp))
        sents = S.load(cached) if cached is not None else S.build_sentences(inp.transcript)
        if cached is None:
            store.put("sentences", key_of("sentences", fp), S.save(sents))
    if provider is None:
        return Outcome("degraded", why or "no_key", sentences=sents)
    if not inp.discovery_strong:
        inp.note(i18n.tr("clip_intel.mode_reason.fast_asr"))
    s = inp.settings
    min_s, max_s = float(s.short_min_seconds), float(s.short_max_seconds)
    # streaming: what early windows already shipped counts; only the rest is wanted here
    need = max(0, inp.limit - len(inp.preshipped))
    early_spans = [(float(p["cand"].start), float(p["cand"].end), f"early:{p['key']}") for p in inp.preshipped]
    try:
        inp.progress(0.05, i18n.tr("clip_intel.progress.topics"))
        with timer("topic_map"):
            tmap = build_topic_map(provider, sents, language=inp.language, store=store, fingerprint=fp,
                                   cancel=inp.cancel_event)
        # the content profile (user's choice, or detected once): editorial guidance for every judgment
        prof = profile.detect(provider, sents, [t.title for t in tmap.topics], store=store, fingerprint=fp,
                              requested=str(getattr(s, "content_profile", "auto") or "auto"))
        provider.guidance = profile.guidance(prof["profile"])
        inp.profile = prof
        # later stages are keyed by the profile too: another profile is another editorial judgment
        fp = key_of("profiled", fp, prof["profile"])
        inp.progress(0.2, i18n.tr("clip_intel.progress.candidates"))
        with timer("candidates"):
            pool, rejected = discover(provider, sents, tmap, language=inp.language, min_s=min_s, max_s=max_s,
                                      store=store, fingerprint=fp, cancel=inp.cancel_event)
        if inp.on_candidates is not None:
            inp.on_candidates(len(pool))
        inp.progress(0.4, i18n.tr("clip_intel.progress.ranking"))
        ranked, chosen, decisions = list(pool), [], []
        if inp.limit > 0:
            with timer("ranking"):
                ranked = ranking.rank_pool(provider, pool, sents, store=store, fingerprint=fp)
                budget = ranking.edit_budget(need, inp.duration or (sents[-1].end if sents else 0.0),
                                             len(ranked)) if need > 0 else 0
                chosen, decisions = ranking.select(ranked, sents, limit=budget, exclude=early_spans,
                                                   horizon=inp.horizon)
    except SemanticError as exc:
        log.warning("semantic pipeline failed: %s", exc)
        return Outcome("degraded", f"model_failed:{exc}", sentences=sents)

    # proper names of the topic map: hints for every re-hearing and "critical" words in the ensemble
    vocab = list(dict.fromkeys(list(s.asr_vocabulary) + list(inp.vocabulary) + list(tmap.names)))
    inp.vocabulary = vocab
    eng = _engines(inp)
    lock = threading.Lock()                   # final_data and the live status are shared between Shorts
    final_path = Path(inp.work_dir) / "transcript.final.json"
    final_data = asr_ensemble.load(final_path)
    for p in inp.preshipped:
        if p.get("final"):
            spans = [list(x) for x in (p["cand"].segments or [(p["cand"].start, p["cand"].end)])]
            final_data["clips"].setdefault(asr_ensemble.span_key(spans), p["final"])
    adjudicate = asr_ensemble.adjudicator(provider)

    plans: list[editor.Plan] = []
    rejected_plans: list[editor.Plan] = []
    # live status of the Shorts in flight: the job shows what each one is doing
    live: dict[str, str] = {}
    counts = {"done": 0, "total": 0}
    here = threading.local()
    t_shorts = time.time()

    def step(text: str) -> None:
        key = getattr(here, "key", "")
        with lock:
            if key:
                live[key] = text
            busy = "; ".join(f"{k}: {v}" for k, v in sorted(live.items()))
            done, total = counts["done"], max(1, counts["total"])
        frac = 0.5 + 0.4 * (done + 0.5 * len(live)) / max(total, done + len(live), 1)
        inp.progress(min(0.9, frac), i18n.tr("clip_intel.progress.shorts", done=done, total=total,
                                             minutes=f"{(time.time() - t_shorts) / 60:.0f}", busy=busy))

    def transcribe_clip(spans: list[list[float]], focus: Optional[list[list[float]]] = None) -> dict[str, Any]:
        k = asr_ensemble.span_key(spans)
        with lock:
            rec = final_data["clips"].get(k)
        if rec is not None and not focus:
            return rec
        with timer("final_transcript"):
            if rec is not None and focus:
                # a repair: re-hear only the disputed words of the focus, with the independent model
                step("re-hearing disputed words")
                rec, fixed = asr_ensemble.refine(rec, focus, eng.get("second") if eng else None, vocabulary=vocab)
                if not fixed:
                    return rec                # nothing new was heard: the editor stops asking
            elif eng:
                rec = asr_ensemble.build_clip([tuple(x) for x in spans], strong=eng["strong"],
                                              second=eng.get("second"), discovery=inp.transcript,
                                              rehear=eng.get("rehear"), adjudicate=adjudicate, vocabulary=vocab,
                                              labels=eng.get("labels"),
                                              families=None if inp.discovery_strong else {"C": "fast"},
                                              progress=lambda m: step("final transcript – " + m))
            else:
                rec = _discovery_only(inp.transcript, spans, vocab)
        with lock:
            final_data["clips"][k] = rec
            asr_ensemble.save(final_path, final_data)
        return rec

    def one_short(c: Cand) -> editor.Plan:
        here.key = c.key
        try:
            return _one_short(c)
        finally:
            with lock:
                live.pop(c.key, None)
                counts["done"] += 1
            here.key = ""
            step("")

    def quick(spans: list[list[float]], focus: Optional[list[list[float]]] = None) -> dict[str, Any]:
        """The clip's words from the discovery transcript (the strong model in Premium) – no ASR run."""
        return _discovery_only(inp.transcript, spans, vocab)

    def _one_short(c: Cand) -> editor.Plan:
        if inp.cancel_event is not None and inp.cancel_event.is_set():
            from ...errors import JobCancelledError

            raise JobCancelledError()
        ck = key_of("short", fp, c.key, c.start_idx, c.end_idx, provider.name, provider.model, min_s, max_s,
                    "gate3")
        hit = store.get(f"short_{c.key}", ck)
        if hit is not None:
            return editor.Plan(cand=c, choice=hit["choice"], final=hit["final"], hook=hit["hook"],
                               history=hit["history"], verdict=hit["verdict"], reason=hit["reason"],
                               scores=hit.get("scores") or {}, repaired=bool(hit.get("repaired")),
                               rejection=hit.get("rejection") or {})
        t = tmap.topic_of(c.start_idx)
        lo, hi = (t.first, t.last) if t is not None else (0, len(sents) - 1)
        lo, hi = max(0, lo - 2), min(len(sents) - 1, hi + 2)
        step("choosing the cut")
        with timer("boundaries"):
            choice = boundaries.optimise(provider, c, sents, lo=lo, hi=hi, min_s=min_s, max_s=max_s)
        overlap = [i for j in tmap.junk if j.get("kind") in ("crosstalk", "unintelligible")
                   for i in range(j["start"], j["end"] + 1) if lo <= i <= hi]
        # the editor judges the moment on the discovery words; the expensive two-model final
        # transcript is made only for a clip that ships (most candidates do not)
        step("final editor")
        wlo, whi = max(0, lo - boundaries.RECON_EXTEND), min(len(sents) - 1, hi + boundaries.RECON_EXTEND)

        def rebuild(p: editor.Plan, critique: str) -> Optional[dict[str, Any]]:
            step("rebuilding the cut")
            with timer("reconstruct"):
                return boundaries.reconstruct(provider, c, sents, p.choice, critique, lo=wlo, hi=whi,
                                              min_s=min_s, max_s=max_s)
        with timer("editor"):
            plan = editor.review(editor.Plan(cand=c, choice=choice, final=quick(choice["spans"]), hook={},
                                             overlap_idx=overlap),
                                 sents, provider, lo=lo, hi=hi, retranscribe=quick, rebuild_hook=lambda p: p.hook,
                                 reconstruct=rebuild,
                                 wide=lambda: boundaries.wide_options(c, sents, wlo, whi, max_s=max_s))
        if plan.verdict == "ship":
            plan.final = transcribe_clip(plan.choice["spans"])
            problems = editor.deterministic_problems(plan, sents)
            if any(x.startswith("critical_unresolved") or x == "loop" for x in problems):
                # a name / number / negation the ensemble could not confirm: re-hear it once
                focus = [[sents[e["idx"]].start, sents[e.get("idx_end", e["idx"])].end] for e in c.evidence]
                plan.final = transcribe_clip(plan.choice["spans"], focus)
                problems = editor.deterministic_problems(plan, sents)
                if any(x.startswith("critical_unresolved") or x == "loop" for x in problems):
                    plan.verdict = "reject"
                    plan.reason += " | final subtitles: " + ",".join(problems)
                    plan.rejection = {"category": "subtitle_uncertainty", "failed_checks": [],
                                      "repaired": plan.repaired, "near_pass": False}
        if plan.verdict in ("ship", "near_pass"):
            step("writing the title")
            with timer("hooks"):
                if getattr(s, "editorial_hook_enabled", False):
                    # the optional on-screen hook text needs its own grounded, ranked candidates
                    plan.hook = hooks.build(provider, editor.plain_text(plan.final), plan.final.get("words") or [],
                                            kind=c.type, language=inp.language)
                else:
                    # default: the editor's shipping answer already carries titles and caption (no extra call)
                    plan.hook = hooks.from_packaging(plan.packaging, editor.plain_text(plan.final),
                                                     plan.final.get("words") or [], language=inp.language,
                                                     fallback_title=c.title)
        if plan.verdict != "unreviewed":
            # a clip the editor could not judge is not cached: a later run judges it (nothing else is redone)
            store.put(f"short_{c.key}", ck, {"choice": plan.choice, "final": plan.final, "hook": plan.hook,
                                             "history": plan.history, "verdict": plan.verdict,
                                             "reason": plan.reason, "scores": plan.scores,
                                             "repaired": plan.repaired, "rejection": plan.rejection})
        if plan.verdict == "ship" and inp.on_ship is not None:
            # time to first result: the renderer starts on this Short while the others are judged
            try:
                inp.on_ship(to_candidate(plan, sents, provider, profile=(inp.profile or {}).get("profile", "")),
                            plan.final)
            except Exception:                       # noqa: BLE001
                log.warning("early render hand-off failed", exc_info=True)
        return plan

    # Shorts in rank order, a few at a time (model calls overlap; the local ASR is serialised);
    # a rejected Short is replaced by the next reserve
    pending = list(chosen)
    unreviewed: list[editor.Plan] = []
    while pending and len(plans) < need:
        batch, pending = pending[:need - len(plans)], pending[need - len(plans):]
        with lock:
            counts["total"] += len(batch)
        step("")
        with ThreadPoolExecutor(max_workers=SHORTS_PARALLEL) as ex:
            results = profiler.pmap(ex, one_short, batch)
        for plan in results:
            if plan.verdict == "unreviewed":
                unreviewed.append(plan)
            elif plan.verdict == "ship" and len(plans) < need:
                plans.append(plan)
            else:
                rejected_plans.append(plan)
    if not plans and need > 0 and not inp.preshipped:
        # salvage: nothing shipped. Before accepting zero, the editor tries the strongest candidates
        # it never saw (the budget ran out on others) and once more the ones it could not reach
        # (provider failures). Content rejections stay rejected; nothing is lowered.
        judged = {p.cand.key for p in rejected_plans}
        retry = [p.cand for p in unreviewed]
        unreviewed = []
        extra = [d["key"] for d in decisions if d["decision"] == "limit" and d["key"] not in judged][:SALVAGE]
        by_key = {c.key: c for c in ranked}
        salvage = retry + [by_key[k] for k in extra if k in by_key]
        if salvage:
            log.info("no Short shipped – salvage pass over %d more candidates", len(salvage))
            with lock:
                counts["total"] += len(salvage)
            with ThreadPoolExecutor(max_workers=SHORTS_PARALLEL) as ex:
                results = profiler.pmap(ex, one_short, salvage)
            for plan in results:
                if plan.verdict == "unreviewed":
                    unreviewed.append(plan)
                elif plan.verdict == "ship" and len(plans) < need:
                    plans.append(plan)
                else:
                    rejected_plans.append(plan)
            for d in decisions:
                if d["key"] in extra:
                    d["decision"] = "salvage"
    near = sorted([p for p in rejected_plans if p.verdict == "near_pass"],
                  key=lambda p: -float(p.cand.scores.get("final", 0.0)))[:max(NEAR_PASS_SHOWN, inp.limit // 3)]

    longforms: list[dict[str, Any]] = []
    longforms_rejected: list[dict[str, Any]] = []
    if inp.want_longform:
        with timer("longform"):
            for t in longform_plan.eligible(tmap, sents, int(getattr(s, "topic_videos_max", 8) or 8)):
                lf = longform_plan.plan_topic(provider, t, sents, tmap, pool,
                                              target_s=float(s.longform_target_seconds), store=store,
                                              fingerprint=fp)
                if lf is not None:
                    lf["shorts"] = [p.cand.key for p in plans if p.cand.topic == t.id]
                    if (lf.get("review") or {}).get("verdict") == "reject":
                        longforms_rejected.append({"topic": t.id, "title": lf.get("title"),
                                                   "reason": lf["review"].get("reason"),
                                                   "checks": lf["review"].get("checks")})
                        continue
                    longforms.append(lf)

    shorts = [p["cand"] for p in inp.preshipped] + \
        [to_candidate(p, sents, provider, profile=(inp.profile or {}).get("profile", "")) for p in plans]
    near_pass = [to_candidate(p, sents, provider, profile=(inp.profile or {}).get("profile", "")) for p in near]
    shipped_keys = {p.cand.key for p in plans}
    report = _report(inp, provider, sents, tmap, pool, rejected, decisions, plans, rejected_plans, longforms,
                     final_data, timer, store)
    report["editor_unreviewed"] = [{"key": p.cand.key, "reason": p.reason} for p in unreviewed]
    report["gate"] = gate_summary(plans, rejected_plans, unreviewed)
    report["longforms_rejected"] = longforms_rejected
    if inp.preshipped:
        report["shipped"] = [{"key": f"early:{p['key']}", "early_window": p.get("window"),
                              "spans": [list(x) for x in (p["cand"].segments or [(p["cand"].start, p["cand"].end)])],
                              "title": p["cand"].title} for p in inp.preshipped] + report["shipped"]
    from . import forensics

    report["forensics"] = forensics.classify(report)
    review = _review(provider, sents, tmap, ranked, decisions, plans, rejected_plans, shipped_keys)
    st = report["final_transcripts"]
    inp.note(i18n.tr("clip_intel.note.semantic_summary", topics=len(tmap.topics), pool=len(pool),
                     selected=len(plans), rejected=len(rejected_plans)))
    if st["words"]:
        inp.note(i18n.tr("clip_intel.note.final_transcript", words=st["words"], disputed=st["regions"],
                         reheard=st["reheard"], unresolved=st["unresolved"]))
    return Outcome("semantic", "", shorts=shorts, longforms=longforms, report=report, review=review,
                   final=final_data, sentences=sents, topic_map=tmap, near_pass=near_pass,
                   unreviewed=len(unreviewed))


def gate_summary(plans, rejected_plans, unreviewed) -> dict[str, Any]:
    """Counts of the final gate: shipped, repaired, rejected by category, not evaluated."""
    cats: dict[str, int] = {}
    for p in rejected_plans:
        c = (p.rejection or {}).get("category") or "other"
        cats[c] = cats.get(c, 0) + 1
    return {"judged": len(plans) + len(rejected_plans), "shipped": len(plans),
            "repaired": sum(1 for p in plans + rejected_plans if p.repaired),
            "shipped_after_repair": sum(1 for p in plans if p.repaired),
            "rejected": len(rejected_plans), "near_pass": sum(1 for p in rejected_plans if p.verdict == "near_pass"),
            "not_evaluated": len(unreviewed), "rejected_by_category": cats}


def _discovery_only(tr: TranscriptResult, spans: Sequence[Sequence[float]], vocab: Sequence[str]) -> dict[str, Any]:
    words = []
    for a, b in spans:
        for w in tr.words_between(a, b):
            words.append(asr_ensemble.FinalWord(w.start, w.end, w.text.strip(), "single", [], ["C"],
                                                asr_ensemble.critical_kind(w.text, vocab), w.probability).to_dict())
    return {"spans": [list(x) for x in spans], "words": words,
            "stats": {"words": len(words), "single": len(words), "agreed": 0, "majority": 0, "adjudicated": 0,
                      "unresolved": 0, "regions": 0, "reheard": 0, "critical_unresolved": 0},
            "loops": [], "hypotheses": {"C": "discovery"}, "seconds": 0.0}


def to_candidate(p: editor.Plan, sents: Sequence[S.Sentence], provider: SemanticProvider, *, profile: str = ""):
    from .. import selection

    c, ch = p.cand, p.choice
    spans = [(float(a), float(b)) for a, b in ch["spans"]]
    closing = sents[c.closing_idx]
    title = (p.hook.get("title") or c.title or "").strip()
    return selection.Candidate(
        start=round(spans[0][0], 3), end=round(spans[-1][1], 3), peak_time=round(closing.start, 3),
        score=round(float(c.scores.get("final", 0.0)), 4), kind="short", title=title[:90],
        description=next((e["quote"] for e in c.evidence if e.get("idx_end", e["idx"]) == c.closing_idx), "")[:300],
        reason=i18n.tr("clip_intel.type." + c.type), category=CATEGORY.get(c.type, "moment"),
        segments=spans if len(spans) > 1 else [], title_source="llm",
        quality={"engine": "semantic", "mode": "semantic", "model": f"{provider.name}:{provider.model}",
                 "profile": profile,
                 "type": c.type, "topic": c.topic, "key": c.key, "evidence": c.evidence, "rubric": c.rubric,
                 "scores": c.scores, "verdicts": c.verdicts[-6:], "boundaries": {
                     "source": ch.get("source"), "reasons": ch.get("reasons"),
                     "start_id": sents[ch["start_idx"]].id, "end_id": sents[ch["end_idx"]].id,
                     "cut_ids": [sents[i].id for i in ch.get("cut_idx") or []]},
                 "editor": {"verdict": p.verdict, "reason": p.reason, "history": p.history,
                            "repaired": p.repaired, "rejection": p.rejection},
                 "final_transcript": p.final.get("stats"),
                 "editorial": {"hook": p.hook.get("hook", ""), "hook_source": p.hook.get("hook_source", ""),
                               "title": title, "candidates": p.hook.get("candidates", [])[:5],
                               "rejected": p.hook.get("rejected", [])[:8],
                               "content_hook": next((e["quote"] for e in c.evidence if e["idx"] == c.opening_idx), "")}})


def _report(inp: Inputs, provider: SemanticProvider, sents, tmap: TopicMap, pool: list[Cand], rejected, decisions,
            plans, rejected_plans, longforms, final_data, timer: _Timer, store: StageStore) -> dict[str, Any]:
    stats = {"words": 0, "regions": 0, "reheard": 0, "unresolved": 0, "critical_unresolved": 0, "adjudicated": 0}
    keys = {asr_ensemble.span_key(p.choice["spans"]) for p in plans}
    for k, rec in (final_data.get("clips") or {}).items():
        if k not in keys:
            continue
        for x in stats:
            stats[x] += int((rec.get("stats") or {}).get(x, 0))
    covered = sum(s.duration for s in sents)
    return {
        "mode": "semantic", "reason": "", "provider": provider.name, "model": provider.model,
        "profile": inp.profile,
        "clips_labelled": True,
        "strong_asr_seconds": round(inp.duration, 1) if inp.discovery_strong else 0.0,
        "semantic_seconds": round(sents[-1].end - sents[0].start, 1) if sents else 0.0,
        "speech_seconds": round(covered, 1),
        "sentences": len(sents), "turns": (sents[-1].turn + 1) if sents else 0,
        "topics": [{"id": t.id, "title": t.title, "start": sents[t.first].start, "end": sents[t.last].end,
                    "spans": [[sents[a].start, sents[b].end] for a, b in t.spans],
                    "long_form_value": t.long_form_value, "short_potential": t.short_potential}
                   for t in tmap.topics],
        "topic_merge": tmap.merge, "chunks": tmap.chunks, "names": tmap.names,
        "junk": [{"start": sents[j["start"]].start, "end": sents[j["end"]].end, "kind": j["kind"]} for j in tmap.junk],
        "pool": [{"key": c.key, "start": round(c.start, 2), "end": round(c.end, 2), "type": c.type, "topic": c.topic,
                  "title": c.title, "rubric_total": c.rubric_total, "scores": c.scores,
                  "evidence": [{"role": e["role"], "t": round(sents[e["idx"]].start, 2), "quote": e["quote"]}
                               for e in c.evidence]} for c in pool],
        "rejected_proposals": rejected[:200],
        "rejected_proposal_count": len(rejected),
        "decisions": decisions,
        "shipped": [{"key": p.cand.key, "spans": p.choice["spans"], "hook": p.hook.get("hook"),
                     "title": p.hook.get("title"), "editor": p.history} for p in plans],
        "editor_rejected": [{"key": p.cand.key, "spans": p.choice["spans"], "reason": p.reason,
                             "category": (p.rejection or {}).get("category"),
                             "kind": (p.rejection or {}).get("kind"),
                             "reconstructed": (p.rejection or {}).get("reconstructed"),
                             "failed_checks": (p.rejection or {}).get("failed_checks"),
                             "repaired": p.repaired, "near_pass": p.verdict == "near_pass",
                             "title": p.cand.title, "score": p.cand.scores.get("final"),
                             "history": p.history} for p in rejected_plans],
        "longforms": [{k: v for k, v in lf.items() if k != "plan"} | {"output_seconds": lf["plan"]["output_seconds"]}
                      for lf in longforms],
        "final_transcripts": stats,
        "usage": provider.usage.to_dict(),
        "cache": store.stats(),
        "resumed_stages": store.stats()["resumed_stages"],
        "timings": timer.seconds,
    }


def _rec(c: Cand, sents, status: str, rejection: Optional[dict[str, Any]], plan: Optional[editor.Plan] = None,
         duplicate_of: Optional[str] = None) -> dict[str, Any]:
    open_ev = next((e for e in c.evidence if e["idx"] == c.opening_idx), c.evidence[0])
    close_ev = next((e for e in c.evidence if e.get("idx_end", e["idx"]) == c.closing_idx), c.evidence[-1])
    rub = c.rubric or {}

    def r(k: str) -> float:
        return round(float((rub.get(k) or {}).get("score", 0)) / 2.0, 3)

    spans = (plan.choice["spans"] if plan else [[c.start, c.end]])
    start, end = spans[0][0], spans[-1][1]
    reasons = lambda k: ([{"key": "model_text", "text": (rub.get(k) or {}).get("reason", ""),  # noqa: E731
                           "params": {"text": (rub.get(k) or {}).get("reason", "")}}]
                         if (rub.get(k) or {}).get("reason") else [])
    fin = (plan.final.get("stats") if plan else None) or {}
    unres = (fin.get("unresolved", 0) / fin["words"]) if fin.get("words") else 0.0
    br = (plan.choice.get("reasons") if plan else {}) or {}
    return {
        "id": c.key, "status": status, "start": round(start, 2), "end": round(end, 2),
        "duration": round(sum(b - a for a, b in spans), 2), "final_score": c.scores.get("final", 0.0),
        "proposed_by": [{"key": "type." + c.type, "text": i18n.tr("clip_intel.type." + c.type)}],
        "hook": {"text": open_ev["quote"], "start": sents[open_ev["idx"]].start, "end": sents[open_ev["idx"]].end,
                 "score": r("hook"), "reasons": reasons("hook"), "problems": []},
        "context": {"text": c.standalone, "sentences": 0, "seconds": 0.0},
        "payoff": {"text": close_ev["quote"], "start": sents[close_ev["idx"]].start, "end": sents[close_ev["idx"]].end,
                   "score": r("payoff"), "reasons": reasons("payoff"), "tail": ""},
        "components": [{"key": f"rubric_{k}", "label": "", "value": r(k)}
                       for k in ("hook", "clarity", "payoff", "interest", "feasibility")]
                      + [{"key": "tournament", "label": "", "value": c.scores.get("rank", 0.0)},
                         {"key": "ship_votes", "label": "", "value": c.scores.get("ship_votes", 0.0)}],
        "penalties": [],
        "boundaries": {"start": round(start, 2), "end": round(end, 2),
                       "start_reason": {"key": "model_text", "params": {"text": br.get("start") or "—"},
                                        "text": br.get("start") or "—"},
                       "end_reason": {"key": "model_text", "params": {"text": br.get("end") or "—"},
                                      "text": br.get("end") or "—"}},
        "low_confidence_words": round(unres, 3),
        "rejection": rejection, "duplicate_of": duplicate_of,
    }


def _review(provider, sents, tmap: TopicMap, ranked: list[Cand], decisions: list[dict[str, Any]], plans, rejected_plans,
            shipped: set[str]) -> dict[str, Any]:
    by_key = {c.key: c for c in ranked}
    plan_of = {p.cand.key: p for p in plans + rejected_plans}
    selected, near, dups = [], [], []
    for p in plans:
        selected.append(_rec(p.cand, sents, "selected", None, p))
    for p in rejected_plans:
        near.append(_rec(p.cand, sents, "near_miss", {"key": "reject.editor", "params": {"reason": p.reason[:200]},
                                                       "text": i18n.tr("clip_intel.reject.editor",
                                                                       reason=p.reason[:200])}, p))
    for d in decisions:
        k, why = d["key"], d["decision"]
        if k in shipped or k in plan_of or why == "selected":
            continue
        c = by_key.get(k)
        if c is None:
            continue
        if why.startswith("duplicate_of:"):
            dups.append(_rec(c, sents, "duplicate", {"key": "reject.duplicate", "text": i18n.tr(
                "clip_intel.reject.duplicate")}, duplicate_of=why.split(":", 1)[1]))
        elif len(near) < 30:
            key = {"judges_rejected": "reject.judges_rejected", "topic_diversity": "reject.topic_diversity",
                   "limit": "reject.limit"}.get(why, "reject.below_quality_bar")
            params = {"score": f"{c.scores.get('final', 0):.2f}", "threshold": f"{ranking.SHIP_THRESHOLD:.2f}"}
            near.append(_rec(c, sents, "near_miss", {"key": key, "params": params,
                                                    "text": i18n.tr("clip_intel." + key, **params)}))
    return {"engine": "semantic", "version": 1, "mode": "semantic",
            "model": f"{provider.name}:{provider.model}", "threshold": ranking.SHIP_THRESHOLD,
            "stats": {"stories": len(ranked), "selected": len(selected), "topics": len(tmap.topics),
                      "near_misses": len(near), "duplicates": len(dups)},
            "topics": [{"id": t.id, "title": t.title, "start": sents[t.first].start, "end": sents[t.last].end}
                       for t in tmap.topics],
            "selected": selected, "near_misses": near, "duplicates": dups}
