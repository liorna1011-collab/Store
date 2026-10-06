# Performance – measured, not estimated

Every number below was measured on the development machine (4 vCPU container, 15.7 GB RAM,
no GPU). The tools are `python -m polixor.bench` (zero paid AI: `POLIXOR_PAID_AI=off`, the
deterministic stand-in editor, the fixture transcript) and `tests/load_bench.py`. Things that
could not be measured here are marked **not measured**, with the reason.

## 1. Source: 10.5 min, package goal (5 Shorts + 3 long-form topic videos)

| | Before (P0) | After (final) | Change |
|---|---|---|---|
| total wall time | 332.1 s | 237.7 s | −28 % |
| realtime factor | 0.527 | 0.377 | |
| analysis | 28.1 s | 26.1 s | |
| **time to first Short** (from Start) | 53.4 s | **46.8 s** | −12 % |
| first Short after generation start | 25.3 s | 20.7 s | |
| time to all Shorts | 137.5 s | 96.1 s | −30 % |
| time to long-form | 332.1 s | 237.6 s | −28 % |
| processes started | 212 | 184 | −13 % |
| full decodes of the source | 2 | 2 | (audio + one shared visual/layout scan) |
| outputs | 5 Shorts, 3 long, 0 failed | 5 Shorts, 3 long, 0 failed | same |

The intermediate runs isolate what each change did:

| Run | Total | First Short | All Shorts |
|---|---|---|---|
| mastering and QC in one pass, 1 render at a time | 261.3 s | 46.7 s | 113.7 s |
| 2 renders at once | 236.8 s | 50.5 s | 91.3 s |
| 2 at once, first Short alone (final) | 237.7 s | 46.8 s | 96.1 s |

With two renders at once, the first Short shared the cores and arrived 3.8 s later. The
final setting renders the first Short alone and then opens the pool.

## 2. Ranked bottlenecks (final run, wall seconds summed over the run)

| # | Step | Wall | Note |
|---|---|---|---|
| 1 | render_long | 141.4 s | x264 encode of 572 s of long-form video, RTF 0.25 |
| 2 | render.ffmpeg (5 Shorts) | 80.1 s | 2 at a time; per-Short encode |
| 3 | select (editor + early renders) | 70.0 s | mostly the early renders running inside it |
| 4 | render.mastering (8 files) | 55.9 s | was 104.7 s |
| 5 | render.qa (8 files) | 29.2 s | was 38.3 s |
| 6 | visual + layout scan | 13.8 s | one decode at 1 fps |
| 7 | audio extraction | 2.1 s | |

What remains is encoding, which is real work. On CPU it only gets faster with a faster
preset, which is not done because quality is not traded for speed, or with a hardware
encoder (section 5).

## 3. Mastering, the fix found by A/B

The single-pass mastering change was compared against the previous code on the same
rendered Shorts:

* On 22 cuts the two agree within 0.13 LU.
* On one rendered Short the new code decided differently at the true-peak edge. Linear
  loudnorm then fell back silently to dynamic mode, and the Short ended at -16.9 LUFS
  instead of -14.
* Fixed: near the edge, the gain + limiter path is used. That Short now measures -14.0
  LUFS, -1.4 dBTP. A regression test covers it
  (`test_mastering_at_the_peak_edge_uses_gain_and_limiter_not_fragile_linear_loudnorm`).

## 4. The website while projects process

`tests/load_bench.py`, 3 projects started through the real upload API, 1 general worker,
every endpoint requested every 0.25 s for the whole run (about 530 requests each):

| Endpoint | p50 | p95 | max |
|---|---|---|---|
| /api/health | 5.6 ms | 10.6 ms | 18.6 ms |
| / (page) | 5.6 ms | 10.6 ms | 19.6 ms |
| /api/projects | 10.3 ms | 17.6 ms | 97.4 ms |
| /api/projects/{id} | 8.7 ms | 16.2 ms | 96.1 ms |

There were no errors. Idle /api/projects: p50 6.1 ms, p95 18.4 ms. All 3 projects completed.
First Shorts arrived at 25.1 s, 49.5 s and 82.0 s. Long-form was deferred twice: each project
got its first results before any long-form rendered.

## 5. Not measured here

* **Speech model (ASR) speed.** The container cannot download models (HuggingFace is blocked
  by the network policy). ASR is planned hardware-aware: CUDA float16 batch 16, or CPU
  int8 batch 8 with threads capped by the container quota. Measure it with
  `python -m polixor.bench --source FILE` on the target machine without `--fixture`.
* **GPU encoders.** There is no GPU here. The test encode correctly found NVENC and QSV
  *listed* by FFmpeg but not working, and chose the CPU. On a GPU machine the admin view
  shows the chosen encoder and the test-encode time.
* **Paid model latency.** Excluded on purpose (no paid calls). The stand-in editor answers
  instantly. With a real model, the editor stage waits on the network while rendering
  continues; that overlap is what the early-render pool is for.

## 6. Hardware recommendations

| Tier | Machine | Price (checked Oct 2026) | Fits |
|---|---|---|---|
| Starter | the current Codespace / any 4 vCPU, 16 GB | – | 1 project at a time, CPU ASR (int8) |
| Growth | 8 dedicated vCPU, 32 GB (e.g. Hetzner CCX33) | about €138/month after the June 2026 repricing | 2 general workers, visual scan overlapped with ASR (8+ cores) |
| High throughput | GPU server, e.g. Hetzner GEX44 (i5-13500 14 cores, 64 GB, RTX 4000 SFF Ada 20 GB) | €184/month + €79 setup | ASR on CUDA float16, NVENC encodes, 2–3 general workers |
| Cloud burst | AWS g6.xlarge (4 vCPU, 16 GB, L4) | about $0.80/hour on demand (us-east-1) | pay per busy hour; small CPU side |

For a production video service, the GPU tier is the one that changes the economics. ASR is
the biggest single stage on long sources, and CTranslate2 on a GPU is typically an order of
magnitude faster than int8 on 4 CPU cores. Measure it with the bench before buying more
than one machine: the throughput claims for GPU ASR above are not measured here.

Sources: Hetzner GEX44 (lowendbox.com, effloow.com), Hetzner CCX33 repricing
(privatedevops.com, spendark.com), AWS g6.xlarge (calculator.holori.com, devzero.io).
