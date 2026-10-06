"""Times the two parts of Whisper large-v3-turbo on this CPU: the encoder per 30-second window and
the decoder per generated token (beam 1 / beam 5), with the int8 model built by build_turbo.py."""
import json
import os
import sys
import time

import ctranslate2
import numpy as np

path = sys.argv[1]
threads = int(sys.argv[2]) if len(sys.argv) > 2 else os.cpu_count()
tokens = int(os.environ.get("TOKENS", "120"))
m = ctranslate2.models.Whisper(path, device="cpu", compute_type="int8", intra_threads=threads, inter_threads=1)
SOT, EOT = 50258, 50257
HE = 50258 + 1 + 20          # <|he|> (index of "he" in the language list + first language id)
TRANSCRIBE = 50359 + 0
vocab_he = HE
out = {"threads": threads, "tokens_per_run": tokens}


def t(fn, n=3):
    fn()
    xs = []
    for _ in range(n):
        a = time.perf_counter()
        fn()
        xs.append(time.perf_counter() - a)
    return min(xs), sum(xs) / len(xs)


for batch in (1, 2, 4):
    feats = ctranslate2.StorageView.from_array(np.random.rand(batch, 128, 3000).astype(np.float32))
    best, avg = t(lambda: m.encode(feats, to_cpu=True), n=2)
    out[f"encode_b{batch}_s"] = round(best, 3)
    out[f"encode_b{batch}_per_window_s"] = round(best / batch, 3)

enc1 = m.encode(ctranslate2.StorageView.from_array(np.random.rand(1, 128, 3000).astype(np.float32)), to_cpu=True)
prompt = [[SOT, 50259 + 20, 50360, 50364]]
for beam in (1, 5):
    def run(beam=beam, n_tok=tokens):
        return m.generate(enc1, prompt, beam_size=beam, max_length=n_tok, suppress_tokens=[EOT],
                          suppress_blank=False, return_scores=False)
    best, avg = t(run, n=2)
    r = run()
    got = len(r[0].sequences_ids[0])
    out[f"decode_beam{beam}_s"] = round(best, 3)
    out[f"decode_beam{beam}_tokens"] = got
    out[f"decode_beam{beam}_ms_per_token"] = round(1000 * best / max(1, got), 2)
print(json.dumps(out))
