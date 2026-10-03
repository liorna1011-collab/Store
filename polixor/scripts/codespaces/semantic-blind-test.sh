#!/usr/bin/env bash
# Codespaces: the blind test of the semantic rebuild (BEFORE 82b3d64 vs AFTER = this version).
#   bash polixor/scripts/codespaces/semantic-blind-test.sh            news interview, then the livestream
#   bash polixor/scripts/codespaces/semantic-blind-test.sh news       only the news interview
#   bash polixor/scripts/codespaces/semantic-blind-test.sh livestream only the livestream
#   bash polixor/scripts/codespaces/semantic-blind-test.sh restart    stop a running test and continue it
#                                                                     from its checkpoints (news, then livestream)
#
# - AFTER generates the content package (Shorts + a long video per topic) with the
#   semantic layer. It uses the Anthropic key you saved in Polixor (Settings → AI);
#   the key is passed to the AFTER server only, never printed or written to disk.
#   Without a key AFTER runs in the labelled degraded mode.
# - BEFORE reuses the AFTER run of the earlier comparison folders when they exist
#   (acceptance_news, acceptance_production) – nothing old is re-run.
# - Everything is checkpointed: run the same command again after a crash and it continues.
# - At the end it packs the files to send back: semantic_results_<name>.zip next to the folders.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"          # .../polixor
TOP="$(cd "$ROOT/.." && pwd)"                                       # the repository / workspace folder
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
D="${POLIXOR_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/polixor}"
WHICH="${1:-all}"
cd "$ROOT" || exit 1
if [ "$WHICH" = "restart" ]; then
    "$PY" scripts/codespaces/semantic_blind_test_helper.py stop
    WHICH=all
fi
# one blind test at a time: a second start (or a double restart) never runs a source twice
if command -v flock >/dev/null; then
    exec 9>"$TOP/.semantic-blind-test.lock"
    if ! flock -n 9; then
        echo "A blind test is already running – watch it with: tail -f $TOP/acceptance_semantic_*/run.log"
        echo "(to stop it and continue from its checkpoints: bash $0 restart)"
        exit 0
    fi
fi

"$PY" scripts/sync_deps.py || { echo "Could not install the new requirements (anthropic)."; exit 1; }
"$PY" scripts/codespaces/semantic_blind_test_helper.py check-key

SETTINGS=()
[ -f "$D/settings.json" ] && SETTINGS=(--settings-from "$D/settings.json")
COMMON=(--language he --set ai_mode=auto --set ai_model=claude-opus-5-5 "${SETTINGS[@]}")

run_one() {                    # name, media, earlier comparison folder
    local name="$1" media="$2" prev="$3" out="$TOP/acceptance_semantic_$1"
    if [ ! -f "$media" ]; then echo "[$name] video not found: $media – skipped"; return; fi
    mkdir -p "$out"
    local before=(--before-ref 82b3d64)
    if [ -f "$prev/after.json" ]; then before=(--before-from "$prev"); fi
    if [ -f "$out/after.json" ] && [ -f "$out/compare.html" ]; then
        echo "[$name] already finished: $out/compare.html"; return
    fi
    echo "[$name] $(date +%H:%M) starting – log: $out/run.log"
    "$PY" scripts/acceptance_compare.py --media "$media" "${COMMON[@]}" "${before[@]}" --out "$out" \
        >> "$out/run.log" 2>&1 9>&-          # the lock stays with this script only
    local rc=$?
    echo "[$name] $(date +%H:%M) finished (exit $rc) – $(tail -1 "$out/run.log")"
    "$PY" scripts/codespaces/semantic_blind_test_helper.py pack "$out" "$TOP/semantic_results_$name.zip" \
        "$ROOT/gold/local"
    echo "[$name] send back: $TOP/semantic_results_$name.zip + polixor_blind_ratings.json from $out/compare.html"
}

NEWS="$TOP/test_media/liberman_news.mp4"
STREAM="$D/sources/9999_1.mp4"
if [ "$WHICH" = "all" ] || [ "$WHICH" = "news" ]; then run_one news "$NEWS" "$TOP/acceptance_news"; fi
if [ "$WHICH" = "all" ] || [ "$WHICH" = "livestream" ]; then
    run_one livestream "$STREAM" "$TOP/acceptance_production"
fi
