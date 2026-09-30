# Meta: Instagram + Facebook — provider notes (Stage 8)

Documentation checked on 2026-09-30, while implementing
`backend/polixor/services/publishing/meta.py`.

The official Meta developer pages were read through search results that
quote them. Re-check this list against the live pages before the first real
post.

## Corrections to the approved plan

| Plan said | Current documentation | What Polixor does |
|---|---|---|
| Instagram: 100 posts per 24 h ("docs also say 50") | **100** API-published posts per rolling 24 h, across all content types. The live quota is in `/{ig-user-id}/content_publishing_limit`. | Checks the quota before creating a container. When the limit is reached it shows a clear "posting limit" message and doesn't retry. |
| Facebook Reels scheduling "disputed" | **Supported**: `video_state=SCHEDULED` + `scheduled_publish_time`, from 10 minutes to 29 days ahead. | Facebook schedules the Reel itself, so Polixor doesn't need to be running. Times outside that window are blocked. |
| Graph video host | `graph-video.facebook.com` is **deprecated**; uploads go to `graph.facebook.com`. | Only `graph.facebook.com` and `rupload.facebook.com` are used. |
| Graph version | Current version is **v26.0** (29 Jul 2026). | `GRAPH_VERSION = "v26.0"`, in one place. |

## Shared connection (Facebook Login for Business)

- One sign-in brings in every Facebook Page the user can post to (the Page's
  `tasks` include `CREATE_CONTENT`) and the Instagram professional
  (Business/Creator) account linked to each Page
  (`instagram_business_account`). Both kinds of account appear under
  Publishing → Accounts; each Instagram account shows which Page it's linked
  to.
- Permissions requested: `pages_show_list`, `pages_read_engagement`,
  `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`.
  After sign-in, Polixor checks which of these were actually granted and asks
  the user to connect again if something is missing.
- PKCE (S256) is used; Meta documents it for manually built login flows.
  Every connection attempt uses a one-time `state`.
- Tokens:
  - The short-lived user token is exchanged for a long-lived one (about 60
    days; `fb_exchange_token`).
  - `/me/accounts` returns a Page token for each Page. A Page token derived
    from a long-lived user token doesn't expire, but can be invalidated (for
    example by a password change or removed permissions): error 190 → the
    account is marked "reconnect needed" and a notification is sent.
  - The long-lived user token is also stored (encrypted), because it's needed
    to upload long Facebook videos. When it expires after about 60 days,
    Polixor asks to connect again.
  - All tokens are encrypted with the local master key and never sent to the
    browser.
- Disconnecting revokes the app's permission (`DELETE /me/permissions`) only
  when it's the last Meta account using that connection. Otherwise the other
  Pages and Instagram accounts would be cut off too.
- Using these permissions for accounts other than the app's own admins and
  testers needs Meta App Review. Until then the app stays in development
  mode.

## Instagram Reels

1. `POST /{ig-user-id}/media` with `media_type=REELS`,
   `upload_type=resumable`, `caption` and `share_to_feed`. An optional
   `thumb_offset` (milliseconds) picks a video frame as the cover.
2. Upload the bytes to the returned `rupload.facebook.com/ig-api-upload/...`
   address, with `Authorization: OAuth`, `offset` and `file_size` headers.
3. Poll the container's `status_code` (`IN_PROGRESS` / `FINISHED` / `ERROR` /
   `EXPIRED` / `PUBLISHED`). The job stays "Processing on the platform" and
   the scheduler checks again every 45 seconds.
4. `POST /{ig-user-id}/media_publish` with `creation_id`, then read the
   `permalink`.

Other facts:

- The API has no scheduling, so Polixor publishes at the scheduled time (the
  server must be running; this is shown in the UI).
- Reels can be up to 15 minutes and 300 MB, MP4 or MOV.
- The caption (title + description + tags as hashtags) is up to 2,200
  characters with at most 30 hashtags.
- `cover_url` needs a publicly reachable image, so it isn't offered. The UI
  offers a cover frame (`thumb_offset`) instead.
- There's no privacy setting, so the UI shows none.

## Facebook

**Reels:**

- `POST /{page-id}/video_reels upload_phase=start` → `video_id` and
  `upload_url`. Then the bytes go to `rupload.facebook.com/video-upload/...`.
  Then `upload_phase=finish` with `video_state` = `PUBLISHED` or `SCHEDULED`
  (+ `scheduled_publish_time`) and a description.
- Requirements: 3–90 seconds, vertical 9:16, at least 540×960. Limit: 30 per
  24 hours.

**Long videos (on the Page):**

- The Resumable Upload API: `POST /{app-id}/uploads` (user token), then
  `POST /upload:{session}` with a `file_offset` header. After an interruption
  Polixor reads `file_offset` from `GET /upload:{session}` and continues from
  there.
- Then `POST /{page-id}/videos` with `fbuploader_video_file_chunk={handle}`,
  `title`, `description`, and `published=false` +
  `scheduled_publish_time` (10 minutes to 6 months ahead) when scheduling.
- The clip's thumbnail goes to `/{video-id}/thumbnails` (`is_preferred`). If
  that fails, the post doesn't.

Pages are always public, so the UI shows no privacy setting.

## No duplicate posts

After each step, the job saves a checkpoint to the database: the container,
`video_id`, upload session, handle, and the published media ID. A retry, a
manual "Try again" or a server restart continues from the last checkpoint:

- A container that already shows `PUBLISHED` (for example when the answer to
  `media_publish` was lost) is matched to the published post by its caption.
  It isn't published again.
- A Facebook video that was already created on the Page is never created a
  second time.
- An upload interrupted by a crash resumes from its checkpoint. Without a
  checkpoint it's marked for the user to check, as before.

## Not verified here

- No real account was used. `tests/test_meta.py` runs against a mocked Graph
  server that fails on any unexpected request. `tests/test_meta_ui.py` blocks
  and records any request to facebook.com, and none was made.
- The error codes for the daily limit (`9` / subcode `2207042`) and the
  rate-limit codes (`4`, `17`, `32`, `613`, `80001–80014`) come from Meta's
  error reference as quoted in secondary sources. Unknown codes are treated
  as a permanent rejection that shows the code.
