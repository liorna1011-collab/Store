import sys, numpy as np, cv2, math
from scipy.io import wavfile
from scipy.signal import stft
from timeline import *
sr, x = wavfile.read(sys.argv[1]); x = x.astype(np.float64)
if x.dtype.kind == 'i' or np.abs(x).max() > 2: x /= np.abs(x).max()
m = x.mean(1)
W, H = 1600, 520
img = np.full((H, W, 3), 18, np.uint8)
# spectrogram (top) - log magnitude
f, t, Z = stft(m, sr, nperseg=1024, noverlap=768)
S = 20 * np.log10(np.abs(Z) + 1e-7); S = np.clip((S + 90) / 80, 0, 1)
S = S[:np.searchsorted(f, 12000)][::-1]
spec = cv2.resize((S * 255).astype(np.uint8), (W, 260), interpolation=cv2.INTER_AREA)
img[0:260] = cv2.applyColorMap(spec, cv2.COLORMAP_MAGMA)
# waveform (bottom) envelope per pixel column
n = len(m); cols = np.array_split(np.abs(x).max(1), W)
for i, c in enumerate(cols):
    a = c.max(); h = int(a * 120)
    cv2.line(img, (i, 390 - h), (i, 390 + h), (200, 120, 255), 1)
events = {'logo': T_LOGO + 0.195, 'click1': T_CLICK1, 'rel1': T_REL1, 'click2': T_CLICK2, 'rel2': T_REL2, 'box out': T_BOX_OUT[0], 'logo out': T_LOGO_OUT[0]}
for k, v in events.items():
    X = int(v / DUR * W)
    cv2.line(img, (X, 0), (X, H), (0, 255, 255), 1)
    cv2.putText(img, k, (X + 3, 500 if 'rel' in k else 480), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
for s in range(6):
    X = int(s / DUR * W); cv2.putText(img, f'{s}s', (X + 2, 275), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)
cv2.imwrite(sys.argv[2], img)
# onset detection around clicks: first sample whose |x| exceeds 20% of the local peak
for k in ['click1', 'rel1', 'click2', 'rel2']:
    t0 = events[k]; i0 = int(t0 * sr)
    seg = np.abs(m[i0 - int(0.03 * sr): i0 + int(0.03 * sr)])
    thr = seg.max() * 0.2
    j = np.argmax(seg > thr) - int(0.03 * sr)
    print(f'{k}: visual {t0*1000:.2f} ms (frame {t0*FPS:.2f}), audio onset {(i0 + j) / sr * 1000:.2f} ms, offset {j / sr * 1000:+.2f} ms')
print('first/last sample', np.abs(x[0]).max(), np.abs(x[-1]).max(), 'duration', len(x) / sr)
