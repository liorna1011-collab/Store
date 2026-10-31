"""Procedural sound design for the RonenGG subscribe overlay (48 kHz stereo).
Every event time comes from timeline.py so audio and picture share one clock."""
import os, sys, math
import numpy as np
from scipy.signal import fftconvolve, butter, sosfilt
from timeline import *

HERE = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(11)
N = int(round(DUR * SR))
mix = np.zeros((N, 2), np.float64)
dry_only = np.zeros((N, 2), np.float64)   # transients kept out of the reverb


def place(sig, t0, gain=1.0, pan=0.0, bus=None):
    """Mono or stereo signal placed at time t0 (s) with constant-power pan (-1..1)."""
    bus = mix if bus is None else bus
    i0 = int(round(t0 * SR))
    if sig.ndim == 1:
        th = (pan + 1) * math.pi / 4
        sig = np.stack([sig * math.cos(th), sig * math.sin(th)], -1)
    s0 = max(0, -i0)
    i0c = max(0, i0)
    n = min(len(sig) - s0, N - i0c)
    if n > 0:
        bus[i0c:i0c + n] += sig[s0:s0 + n] * gain


def tvec(dur):
    return np.arange(int(round(dur * SR))) / SR


def svf_bandpass(x, fc, q):
    """Time-varying Chamberlin state-variable band-pass. fc: array (Hz) or scalar."""
    fc = np.broadcast_to(np.asarray(fc, np.float64), x.shape)
    f = 2 * np.sin(np.pi * np.clip(fc, 20, SR / 6) / SR)
    damp = 1.0 / q
    low = band = 0.0
    out = np.empty_like(x)
    for i in range(len(x)):
        high = x[i] - low - damp * band
        band += f[i] * high
        low += f[i] * band
        out[i] = band
    return out


def hp(x, fc, order=2):
    return sosfilt(butter(order, fc, 'hp', fs=SR, output='sos'), x)


def lp(x, fc, order=2):
    return sosfilt(butter(order, fc, 'lp', fs=SR, output='sos'), x)


def norm(x):
    return x / (np.abs(x).max() + 1e-12)


# ------------------------------------------------------------------ building blocks
def whoosh(dur, t_peak, f0, f1, f2, q=1.6, shape=2.0, tail=0.25, seed=0, stereo_width=0.35, pan0=0.0, pan1=0.0):
    """Filtered-noise whoosh: cutoff f0 -> f1 (at peak) -> f2, swelling envelope."""
    t = tvec(dur)
    r = np.random.default_rng(seed)
    u = t / dur
    up = np.clip(t / t_peak, 0, 1)
    down = np.clip((t - t_peak) / max(1e-3, dur - t_peak), 0, 1)
    fc = np.where(t < t_peak, f0 * (f1 / f0) ** (up ** 1.2), f1 * (f2 / f1) ** down)
    env = np.where(t < t_peak, up ** shape, np.exp(-(np.clip(t - t_peak, 0, None) / tail) ** 1.4))
    env *= np.clip(t / 0.01, 0, 1) * np.clip((dur - t) / 0.03, 0, 1)
    nL = r.standard_normal(len(t)); nR = r.standard_normal(len(t))
    nC = r.standard_normal(len(t))
    a = stereo_width
    L = svf_bandpass(nC * (1 - a) + nL * a, fc, q) * env
    R = svf_bandpass(nC * (1 - a) + nR * a, fc, q) * env
    pan = pan0 + (pan1 - pan0) * u
    th = (pan + 1) * math.pi / 4
    sig = np.stack([L * np.cos(th) * 1.414, R * np.sin(th) * 1.414], -1)
    return sig / (np.abs(sig).max() + 1e-9)


def mouse_click(release=False, seed=0):
    """Tactile micro-switch click: noise transient + plastic body resonances."""
    r = np.random.default_rng(seed)
    t = tvec(0.06)
    k = 1.18 if release else 1.0
    tr = r.standard_normal(len(t)) * np.exp(-t / 0.0007)
    tr = hp(tr, 1800)
    res = (1.00 * np.sin(2 * np.pi * 2350 * k * t) * np.exp(-t / 0.0065) +
           0.55 * np.sin(2 * np.pi * 3900 * k * t + 0.7) * np.exp(-t / 0.0042) +
           0.30 * np.sin(2 * np.pi * 6100 * k * t + 1.3) * np.exp(-t / 0.0025))
    body = 0.55 * np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.011) * (0 if release else 1)
    s = 0.9 * tr + 0.45 * res + body
    s *= np.clip(t / 0.00015, 0, 1)
    return norm(s) * (0.55 if release else 1.0)


def neon_pulse(seed=0):
    """Short synth energy pulse: rising sweep with chorus + airy shimmer + soft sub."""
    t = tvec(0.75)
    f = 170 * (4.6 ** np.clip(t / 0.16, 0, 1))  # 170 -> 780 Hz
    ph = 2 * np.pi * np.cumsum(f) / SR
    env = np.clip(t / 0.008, 0, 1) * np.exp(-t / 0.20)
    tone = (np.sin(ph) + 0.45 * np.sin(2 * ph + 0.3) + 0.2 * np.sin(3 * ph + 1.1)) * env
    det = (np.sin(ph * 1.007 + 0.5) + 0.4 * np.sin(2 * ph * 1.007)) * env
    tone = lp(tone + 0.7 * det, 3500)
    r = np.random.default_rng(seed)
    sh = svf_bandpass(r.standard_normal(len(t)), 6500 - 2500 * np.clip(t / 0.4, 0, 1), 3.0) * np.exp(-t / 0.12) * np.clip(t / 0.004, 0, 1)
    sub = np.sin(2 * np.pi * (62 - 18 * np.clip(t / 0.12, 0, 1)) * t) * np.exp(-t / 0.09) * np.clip(t / 0.004, 0, 1)
    s = 0.55 * norm(tone) + 0.28 * norm(sh) + 0.45 * sub
    L = s + 0.10 * np.roll(norm(det), 90)
    R = s - 0.10 * np.roll(norm(det), 90)
    st = np.stack([L, R], -1)
    return st / np.abs(st).max()


def chime(f0, dur=1.6, bright=1.0):
    """Struck metal-bar / bell tone with slightly inharmonic partials."""
    t = tvec(dur)
    parts = [(1.0, 1.00, 0.42), (2.0, 0.42, 0.24), (2.76, 0.22 * bright, 0.14), (4.07, 0.16 * bright, 0.08), (5.40, 0.08 * bright, 0.05)]
    s = np.zeros_like(t)
    for ratio, a, tau in parts:
        s += a * np.sin(2 * np.pi * f0 * ratio * t + ratio) * np.exp(-t / tau)
    strike = hp(np.random.default_rng(int(f0)).standard_normal(len(t)), 4000) * np.exp(-t / 0.0018) * 0.25
    s = (s + strike) * np.clip(t / 0.0015, 0, 1)
    return norm(s)


def sparkle(dur, density, fmin, fmax, seed=0, decay=(0.02, 0.07)):
    t = tvec(dur)
    s = np.zeros((len(t), 2))
    r = np.random.default_rng(seed)
    n = int(density * dur)
    for _ in range(n):
        i0 = r.integers(0, max(1, len(t) - 2000))
        f = r.uniform(fmin, fmax)
        tau = r.uniform(*decay)
        g = r.uniform(0.3, 1.0)
        L = int(min(len(t) - i0, tau * 6 * SR))
        tt = np.arange(L) / SR
        grain = g * np.sin(2 * np.pi * f * tt) * np.exp(-tt / tau) * np.clip(tt / 0.001, 0, 1)
        p = r.uniform(-0.8, 0.8)
        th = (p + 1) * math.pi / 4
        s[i0:i0 + L, 0] += grain * math.cos(th)
        s[i0:i0 + L, 1] += grain * math.sin(th)
    env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 0.7
    return s * env[:, None] / (np.abs(s).max() + 1e-9)


def thump(f0=70, f1=42, dur=0.25, tau=0.07):
    t = tvec(dur)
    f = f1 + (f0 - f1) * np.exp(-t / 0.03)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t / tau) * np.clip(t / 0.003, 0, 1)


# ------------------------------------------------------------------ the mix
db = lambda x: 10 ** (x / 20)

# Phase A: entrance
place(whoosh(0.62, 0.21, 280, 2600, 700, q=1.4, tail=0.16, seed=1, pan0=-0.75, pan1=-0.2), T_LOGO - 0.02, db(-13))
logo_land = T_LOGO + 0.195
place(thump(78, 44, 0.3, 0.08), logo_land, db(-15))
place(sparkle(0.35, 70, 3500, 9000, seed=2), logo_land - 0.02, db(-29))
place(whoosh(0.46, 0.22, 500, 3600, 1200, q=2.0, tail=0.12, seed=3, pan0=-0.3, pan1=0.15), T_PANEL - 0.02, db(-22))
place(whoosh(0.42, 0.18, 900, 4800, 1600, q=2.2, tail=0.10, seed=4, pan0=0.8, pan1=0.45), T_BOX - 0.03, db(-24))
place(chime(2637, 0.5, 0.5), T_BOX + 0.19, db(-34), pan=0.45)
place(sparkle(0.55, 90, 2500, 7000, seed=5), T_BAND[0] - 0.03, db(-30))
# Phase B: cursor + subscribe click
place(whoosh(0.50, 0.25, 2000, 5200, 2600, q=1.2, tail=0.12, seed=6, pan0=0.55, pan1=0.1), T_CUR_MOVE1[0], db(-31))
place(mouse_click(False, 7), T_CLICK1, db(-6), pan=0.05, bus=dry_only)
place(mouse_click(True, 8), T_REL1, db(-9), pan=0.05, bus=dry_only)
place(neon_pulse(9), T_CLICK1 + 0.004, db(-12))
place(sparkle(0.42, 120, 4000, 10000, seed=10, decay=(0.01, 0.04)), T_CLICK1 + 0.01, db(-27))
place(chime(1568, 0.6, 0.4), T_CHECK[0] + 0.05, db(-33), pan=-0.1)   # tiny tick as the check draws
# Phase C: bell
place(whoosh(0.50, 0.25, 2200, 5600, 2800, q=1.2, tail=0.12, seed=11, pan0=0.0, pan1=0.45), T_CUR_MOVE2[0], db(-31))
place(mouse_click(False, 12), T_CLICK2, db(-6), pan=0.4, bus=dry_only)
place(mouse_click(True, 13), T_REL2, db(-9), pan=0.4, bus=dry_only)
place(chime(1318.5, 1.5), T_CLICK2 + 0.006, db(-13), pan=0.35)
place(chime(1975.5, 1.3), T_CLICK2 + 0.092, db(-15.5), pan=0.38)
place(chime(1318.5, 1.0, 0.7), T_SWING + 0.16, db(-24), pan=0.3)     # clapper swing-back
place(thump(95, 50, 0.35, 0.09), T_CLICK2 + 0.004, db(-17), pan=0.3)
place(whoosh(0.5, 0.05, 1800, 1400, 500, q=0.9, tail=0.18, seed=14, pan0=0.3, pan1=0.3), T_CLICK2, db(-29))
place(whoosh(0.45, 0.22, 2400, 5000, 2400, q=1.2, tail=0.12, seed=15, pan0=0.45, pan1=0.9), T_CUR_OUT[0], db(-35))
# Phase E: exit
place(sparkle(0.32, 110, 3000, 9500, seed=16), T_BAND_OUT[0], db(-28))
place(whoosh(0.40, 0.30, 600, 4200, 2000, q=1.5, shape=2.4, tail=0.08, seed=17, pan0=0.4, pan1=0.95), T_BOX_OUT[0], db(-16))
place(whoosh(0.42, 0.33, 400, 3000, 1500, q=1.5, shape=2.4, tail=0.08, seed=18, pan0=0.1, pan1=-0.45), T_PANEL_OUT[0], db(-18))
place(whoosh(0.52, 0.40, 250, 2400, 900, q=1.4, shape=2.4, tail=0.10, seed=19, pan0=-0.4, pan1=-0.95), T_LOGO_OUT[0], db(-15))

# gentle room: short synthetic stereo reverb on the tonal/whoosh bus
ir_t = tvec(0.55)
irL = np.random.default_rng(20).standard_normal(len(ir_t)) * np.exp(-ir_t / 0.13)
irR = np.random.default_rng(21).standard_normal(len(ir_t)) * np.exp(-ir_t / 0.13)
irL = lp(irL, 6000); irR = lp(irR, 6000)
irL /= np.sqrt((irL ** 2).sum()); irR /= np.sqrt((irR ** 2).sum())
wetL = fftconvolve(mix[:, 0], irL)[:N]
wetR = fftconvolve(mix[:, 1], irR)[:N]
out = mix + dry_only + 0.16 * np.stack([wetL, wetR], -1)
out = hp(out.T, 30).T  # remove sub-rumble / DC

# fade in/out guards so first and last samples are silent
fade = np.ones(N)
fi = int(0.004 * SR); fo = int(0.03 * SR)
fade[:fi] = np.linspace(0, 1, fi)
fade[-fo:] = np.linspace(1, 0, fo)
out *= fade[:, None]
peak = np.abs(out).max()
out = out / peak * db(-1.5)

from scipy.io import wavfile
os.makedirs(os.path.join(HERE, 'build'), exist_ok=True)
wavfile.write(os.path.join(HERE, 'build', 'mix_f32.wav'), SR, out.astype(np.float32))
print('samples', N, 'peak dBFS', 20 * math.log10(np.abs(out).max()), 'rms dBFS', 20 * math.log10(np.sqrt((out ** 2).mean())))
