"""Single source of truth for timing (video + audio) and easing curves."""
import math
import numpy as np

FPS = 60
NFRAMES = 300            # 5.000 s
DUR = NFRAMES / FPS
SR = 48000

# ---------------------------------------------------------------- key times (s)
# Phase A - entrance
T_STREAKS = (0.00, 0.55)
T_LOGO = 0.05            # logo starts flying in from the left
T_LOGO_LAND = 0.40       # first crossing of the rest position (impact accent)
T_PANEL = 0.22           # panel slides out from behind the logo
T_BOX = 0.34             # bell box enters
T_BAND = (0.45, 0.95)    # energy band ignites behind the parts
# Phase B - subscribe click
T_CUR_IN = 0.95
T_CUR_MOVE1 = (1.00, 1.47)
T_CLICK1 = 93 / FPS      # 1.550 s  (frame 93)
T_REL1 = 98 / FPS        # mouse-up
T_SWEEP = (T_CLICK1 + 0.03, T_CLICK1 + 0.30)
T_FLIP = (T_CLICK1 + 0.05, T_CLICK1 + 0.27)
T_CHECK = (T_CLICK1 + 0.20, T_CLICK1 + 0.36)
# Phase C - bell
T_CUR_MOVE2 = (1.92, 2.40)
T_CLICK2 = 150 / FPS     # 2.500 s  (frame 150)
T_REL2 = 155 / FPS
T_SWING = T_CLICK2 + 0.045
T_CUR_OUT = (2.72, 3.12)
# Phase D - hold
T_HOLD = (3.30, 4.00)
# Phase E - exit
T_BAND_OUT = (3.86, 4.08)
T_BOX_OUT = (4.02, 4.34)
T_PANEL_OUT = (4.14, 4.50)
T_LOGO_OUT = (4.36, 4.80)
T_END = 4.90             # everything gone


# ---------------------------------------------------------------- curves
def clamp01(u):
    return max(0.0, min(1.0, u))


def lin(t, t0, t1):
    return clamp01((t - t0) / (t1 - t0))


def smooth(u):
    u = clamp01(u)
    return u * u * (3 - 2 * u)


def smoother(u):
    u = clamp01(u)
    return u * u * u * (u * (u * 6 - 15) + 10)


def ease_out_cubic(u):
    u = clamp01(u)
    return 1 - (1 - u) ** 3


def ease_in_cubic(u):
    u = clamp01(u)
    return u ** 3


def ease_in_quart(u):
    u = clamp01(u)
    return u ** 4


def ease_out_expo(u):
    u = clamp01(u)
    return 1.0 if u >= 1 else 1 - 2 ** (-10 * u)


def ease_in_out_cubic(u):
    u = clamp01(u)
    return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def spring(t, zeta=0.62, freq=2.2, v0=6.0):
    """Damped spring from 0 to 1 starting at t=0 with initial velocity v0 (units/s).
    Natural overshoot + settle. Returns 0 for t<=0."""
    if t <= 0:
        return 0.0
    w = 2 * math.pi * freq
    wd = w * math.sqrt(1 - zeta * zeta)
    c1 = -1.0
    c2 = (v0 + zeta * w * c1) / wd
    e = math.exp(-zeta * w * t) * (c1 * math.cos(wd * t) + c2 * math.sin(wd * t))
    return 1.0 + e


def damped_sin(t, amp, freq, tau):
    if t <= 0:
        return 0.0
    return amp * math.exp(-t / tau) * math.sin(2 * math.pi * freq * t)


def min_jerk(u):
    u = clamp01(u)
    return u ** 3 * (10 - 15 * u + 6 * u * u)


def bezier2(p0, p1, p2, u):
    a = (1 - u) ** 2
    b = 2 * (1 - u) * u
    c = u * u
    return (a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1])
