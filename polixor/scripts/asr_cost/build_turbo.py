"""Builds a CTranslate2 Whisper model with the exact geometry of large-v3-turbo and random weights
(the real weights cannot be downloaded here). Compute cost per window/token is a function of the
geometry and the quantization, not of the weight values – that is what this measures."""
import sys
import numpy as np
from ctranslate2.specs import whisper_spec, common_spec, transformer_spec

D, ENC, DEC, HEADS, MELS, VOCAB = 1280, 32, 4, 20, 128, 51866
out = sys.argv[1]
quant = sys.argv[2] if len(sys.argv) > 2 else "int8"
rng = np.random.default_rng(0)


def r(*shape):
    return (rng.standard_normal(shape, dtype=np.float32) * 0.02).astype(np.float16)


def ln(x):
    x.gamma = np.ones(D, np.float16)
    x.beta = np.zeros(D, np.float16)


def lin(x, o, i):
    x.weight = r(o, i)
    x.bias = np.zeros(o, np.float16)


def ffn(f):
    ln(f.layer_norm)
    lin(f.linear_0, 4 * D, D)
    lin(f.linear_1, D, 4 * D)


spec = whisper_spec.WhisperSpec(ENC, HEADS, DEC, HEADS)
e = spec.encoder
e.conv1.weight, e.conv1.bias = r(D, MELS, 3), np.zeros(D, np.float16)
e.conv2.weight, e.conv2.bias = r(D, D, 3), np.zeros(D, np.float16)
e.position_encodings.encodings = r(1500, D)
ln(e.layer_norm)
for L in e.layer:
    ln(L.self_attention.layer_norm)
    lin(L.self_attention.linear[0], 3 * D, D)
    lin(L.self_attention.linear[1], D, D)
    ffn(L.ffn)
d = spec.decoder
d.embeddings.weight = r(VOCAB, D)
d.position_encodings.encodings = r(448, D)
ln(d.layer_norm)
d.projection.weight = d.embeddings.weight
d.projection.bias = np.zeros(VOCAB, np.float16)
for L in d.layer:
    ln(L.self_attention.layer_norm)
    lin(L.self_attention.linear[0], 3 * D, D)
    lin(L.self_attention.linear[1], D, D)
    ln(L.attention.layer_norm)
    lin(L.attention.linear[0], D, D)
    lin(L.attention.linear[1], 2 * D, D)
    lin(L.attention.linear[2], D, D)
    ffn(L.ffn)

specials = ["<|endoftext|>", "<|startoftranscript|>"]
langs = ["en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr", "pl", "ca", "nl", "ar", "sv", "it", "id", "hi",
         "fi", "vi", "he"]
tokens = [f"t{i}" for i in range(50257)] + specials + [f"<|{l}|>" for l in langs]
tokens += [f"<|lang{i}|>" for i in range(100 - len(langs))]
tokens += ["<|translate|>", "<|transcribe|>", "<|startoflm|>", "<|startofprev|>", "<|nospeech|>", "<|notimestamps|>"]
tokens += [f"<|{i * 0.02:.2f}|>" for i in range(VOCAB - len(tokens))]
assert len(tokens) == VOCAB, len(tokens)
spec.register_vocabulary(tokens)
spec.config.suppress_ids = []
spec.config.suppress_ids_begin = [220, 50257]
spec.config.lang_ids = [tokens.index(f"<|{l}|>") for l in langs]
spec.config.alignment_heads = [(2, 4), (3, 7)]
spec.validate()
spec.optimize(quantization=quant)
spec.save(out)
print("saved", out, quant)
