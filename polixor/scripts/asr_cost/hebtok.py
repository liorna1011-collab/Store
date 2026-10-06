import base64, json, sys, tiktoken
ranks = {base64.b64decode(t): int(r) for t, r in (l.split() for l in open(sys.argv[1]) if l.strip())}
enc = tiktoken.Encoding(name="multilingual", explicit_n_vocab=len(ranks), pat_str=r"""'s|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)|\s+""", mergeable_ranks=ranks, special_tokens={})
for f in sys.argv[2:]:
    d = json.load(open(f))
    segs = d["segments"]
    text = "".join(" " + s["text"].strip() for s in segs)
    speech = sum(s["end"] - s["start"] for s in segs)
    span = segs[-1]["end"] - segs[0]["start"]
    n = len(enc.encode(text))
    words = len(text.split())
    print(json.dumps({"file": f.split("/")[-1], "language": d.get("language"), "tokens": n, "words": words,
                      "speech_seconds": round(speech, 1), "span_seconds": round(span, 1),
                      "tokens_per_word": round(n / words, 2), "tokens_per_speech_second": round(n / speech, 2),
                      "tokens_per_30s_window_of_speech": round(30 * n / speech)}))
