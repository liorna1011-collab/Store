"""
The semantic intelligence pipeline (the primary decision layer).

    strong full-source transcript
      → sentences and turns (sentences.py, deterministic, with stable IDs)
      → chunk topic maps with overlap → global topic map (topics.py)
      → typed candidate proposals per topic (candidates.py)
      → deterministic validation of every model output (validate.py)
      → global ranking: absolute rubric + shuffled listwise tournament,
        dedupe and topic diversity (ranking.py)
      → boundary optimisation over sentence combinations, internal cuts
        (boundaries.py)
      → final per-clip transcript (services/asr_ensemble.py)
      → editorial hooks and titles (hooks.py)
      → final editor with repair (editor.py)
      → Shorts and long-form topic videos from the same map (longform_plan.py)

Every model answer must cite sentence IDs, timestamps and exact quotes; the
code rejects anything it cannot verify against the transcript. Every stage is
checkpointed (checkpoint.py) and the model's answers are cached by prompt, so
a crash or a regeneration never redoes paid or slow work.

Without a usable language model the old phrase/audio engine runs instead as
a *degraded* fallback, and every clip and report says so (degraded.py).
"""
