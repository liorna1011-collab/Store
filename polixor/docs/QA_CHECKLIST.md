# Click-through QA checklist

PASS = verified in a real browser (Chromium, `e2e_hardening.py`) or by an automated test;
FIXED = was broken, fixed in this pass and verified; NOT IMPLEMENTED = not built (stated, not hidden).
Every page was also opened at 1366×900 and 390×844 in English and Hebrew: correct direction (LTR / RTL),
no horizontal scroll, no page errors.

## Access
| Action | Result |
|---|---|
| Wrong password → message | PASS |
| Sign in → home | PASS |
| Session expires → "Your session expired. Sign in again." → sign in → back on the same screen | FIXED (was an instant redirect losing the screen) |
| Sign out | PASS (unchanged) |

## Home (dashboard)
| Action | Result |
|---|---|
| Plan card: plan, "Used X of N minutes", remaining, renewal date – minutes only | PASS (new) |
| Project list, newest first; "Show more" pages of 30 | FIXED (was 60 at once, ~4 queries per project) |
| Live progress on cards without re-render storms | FIXED |
| Delete project (confirm) | PASS |
| Back / forward | PASS |

## New project
| Action | Result |
|---|---|
| Choose file → chunked upload with progress | PASS |
| Pause / Resume | PASS – note: Pause stops the parts in flight, so the bar returns to what the server has confirmed (on a slow link with no part finished yet, 0%); Resume sends them again |
| Refresh mid-upload → choose the same file → continues from the server's parts | PASS |
| Cancel / remove file | PASS |
| After upload: "This video will use 1:30 of your 300 remaining minutes" | PASS (new) |
| Over quota: "You have 1:00 minutes remaining. This video is 1:30 minutes." + Start disabled | PASS (new) |
| Double-click Start → one project | FIXED (server-side unique key; was racy) |
| Link import (check, section, live capture) | PASS (unchanged; covered by test_ingest / test_projects) |

## Project
| Action | Result |
|---|---|
| Processing progress; leave and come back; refresh | PASS |
| Analysis → generation by itself | PASS |
| Cancel | PASS (refund per cancel policy – test) |
| Stuck stage → "Needs attention" with the stage → Resume (double click = one run, no new charge) | PASS (new) |
| Failed (other) → message + Retry | PASS |
| Re-analyze | PASS |
| Diagnostics (timings, gate) – no model calls/tokens for customers | FIXED (model data moved to admin) |

## Results
| Action | Result |
|---|---|
| Shorts / Long-form tabs; ready up front, others collapsed | PASS |
| Video players load only when played (`preload="none"`, poster) | FIXED (every MP4 was fetched on open) |
| Play (HTTP 206 range, full decode) | PASS |
| Download one clip | PASS |
| Download all Shorts / long-form / package (streamed ZIP) | FIXED (temp-file race, nothing sent until built) |
| QA mode: rate → saved → kept after reload | PASS |
| Export ratings (JSON) | PASS |
| Polling while running: one at a time, visible tab only | FIXED |

## Clip editor
| Action | Result |
|---|---|
| Export (re-render) returns at once; renders in the background | FIXED (rendered inside the request) |
| Leave during export and return → follows it to the end | PASS (new) |
| Double-click Export → one render | FIXED |
| FFmpeg failure → clear message, previous clip kept | FIXED |
| Save subtitles / details | PASS (no charge – test) |
| Download | PASS |

## Settings
| Action | Result |
|---|---|
| Plan card with this period's usage per project | PASS (new) |
| Change → Save → reload keeps it | PASS |
| AI key: saved encrypted, never shown | PASS (unchanged) |

## Other pages
| Page | Result |
|---|---|
| Clips gallery, Publishing, Images | PASS (render, RTL/LTR, mobile, no errors; behaviour unchanged) |
| Admin `/admin`: wrong token refused; costs, margin, ledger, uploads, health, per-stage resources | PASS (new) |

## Failure paths
| Situation | Result |
|---|---|
| Network drops → "Cannot reach the Polixor server" + customer hint | FIXED (hint told users to run a script) |
| Network drops while opening a screen not loaded yet → message + Try again | FIXED (blank screen – found by this test) |
| Back online → works | PASS |
| Request that never answers → timeout message | FIXED (no timeout before) |
| Server error → plain message, no stack trace / path | PASS (test) |
| Database busy → waits, answers | PASS (test) |
| Worker crash → failed cleanly, refund before the transcript | PASS (test) |
| Server restart mid-job → resumes; mid-re-export → reported, previous clip kept | PASS (test) |
| Disk nearly full → upload refused up front (507) | PASS (test) |
| Model unavailable / no credits → "not evaluated", never a rejection | PASS (test_semantic) |
| One clip render fails → the others finish | PASS (test_production) |

## Not implemented
| Item | Note |
|---|---|
| Measured production speed targets (section 54) | needs the target machine (GPU) and paid model calls – not allowed in this pass |
| Multi-account billing | single "default" account; model ready (`account_id`) |
| Payment / plan upgrade flow for customers | plans are set in the admin view |
