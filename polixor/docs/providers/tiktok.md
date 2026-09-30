# TikTok — provider notes (Stage 9)

Documentation checked on 2026-09-30, while implementing
`backend/polixor/services/publishing/tiktok.py`.

The TikTok developer pages were read through search results that quote them.
Re-check this list against the live pages before the first real post.

## Corrections to the approved plan

| Plan said | Current documentation | What Polixor does |
|---|---|---|
| "no PKCE on web" | Login Kit **supports PKCE** (`code_challenge` + `code_challenge_method=S256`; optional on web, required on desktop). TikTok's `code_challenge` is the **SHA-256 of the verifier in hex**, not the standard base64url. | PKCE on every connection; the provider supplies its own challenge function (`hex_challenge`). |
| Token 24 h / refresh 365 days | Confirmed: `expires_in` 86400, `refresh_expires_in` 31536000. | Refreshed automatically before posting. A refresh that fails → "reconnect needed" plus a notification. |

## Connection

- `https://www.tiktok.com/v2/auth/authorize/` with `client_key`, the scopes
  `user.info.basic,video.publish`, `state` and the PKCE challenge.
- Token and refresh: `POST open.tiktokapis.com/v2/oauth/token/`.
- Revoke when disconnecting: `POST /v2/oauth/revoke/`.
- The connection is refused if `video.publish` wasn't granted.

## Posting (Direct Post, `FILE_UPLOAD`)

1. **Creator info.** `creator_info/query` is read every time the Publish
   dialog opens, and again right before posting. It returns the nickname
   (shown to the user), the allowed privacy levels, whether the creator
   turned off comments, duets or stitches, and
   `max_video_post_duration_sec`. A video longer than that maximum is
   blocked before anything is created.
2. **Initialise.** `video/init` sends `post_info`: the caption (title +
   description + hashtags, up to 2,200 characters), `privacy_level`,
   `disable_comment`/`duet`/`stitch` (on unless the user ticked them),
   `brand_content_toggle`, `brand_organic_toggle`, `is_aigc`, and
   `video_cover_timestamp_ms` for the cover frame. It also sends
   `source_info` (`FILE_UPLOAD`, `video_size`, `chunk_size`,
   `total_chunk_count`).
3. **Upload.** Chunks are sent **in order** with `PUT {upload_url}`,
   `Content-Range` and `Content-Type: video/mp4`.
   - Chunks are 5–64 MB; the last chunk may be up to 128 MB.
   - The chunk count is the file size divided by the chunk size, rounded
     down.
   - A file under 5 MB is sent as a single chunk.
4. **Status.** `status/fetch` is polled until `PUBLISH_COMPLETE`. The link
   uses `publicaly_available_post_id` (TikTok's spelling) when it's present;
   right after publishing it may not be, because moderation isn't done yet.
   Until then the link falls back to the creator's profile.

There's no scheduling in the API, so Polixor publishes at the scheduled time
and the server must be running (the UI says so).

## TikTok's content-sharing guidelines, as implemented in the Publish dialog

- The creator's nickname comes from the live creator info: "Posting to TikTok
  as …".
- Privacy: the user must pick; there's no default, and only the levels TikTok
  returned are offered.
- Comment, duet and stitch start unticked, and are disabled when the creator
  turned them off.
- Commercial content disclosure:
  - When it's on, the user must choose "Your brand" and/or "Branded content".
  - The label shown is "Promotional content" or "Paid partnership".
  - Branded content can't be "Only me".
- The Music Usage Confirmation line (plus the Branded Content Policy when
  it's branded content) is shown before posting, along with a note that
  processing may take a few minutes.
- There's an AI-generated content label (`is_aigc`).
- YouTube-only options (made for kids) aren't shown for TikTok.

## Audit, limits and errors

- **Unaudited app:** every post must be `SELF_ONLY`, and TikTok also requires
  the creator's account to be private
  (`unaudited_client_can_only_post_to_private_accounts`). Until
  *Settings → Publishing → "My TikTok app passed TikTok's audit"* is on, the
  dialog offers only "Only me" and shows a warning.
- **Limits:** 6 requests per minute per user token, and a daily post cap per
  account (about 15–25; `spam_risk_too_many_posts`). The daily cap becomes a
  "posting limit" message without automatic retries. `rate_limit_exceeded`
  and 5xx errors are retried.
- **Other mapped errors:** `privacy_level_option_mismatch`,
  `scope_not_authorized`, `access_token_invalid`. Unknown codes become a
  permanent rejection that shows the code.

## No duplicate posts

After each step Polixor saves a checkpoint: `publish_id`, `upload_url` and
the number of chunks sent.

- A retry after a dropped chunk continues from the next chunk.
- Once every chunk has been uploaded, the `publish_id` is never initialised
  again; Polixor only checks its status.
- An expired upload URL (valid for 1 hour) or a `FAILED` status clears the
  checkpoint, so the next attempt starts a new `publish_id`. The old one was
  never published.

## Not verified here

No real account was used. `tests/test_tiktok.py` runs against a mocked
TikTok server that fails on any unexpected request. `tests/test_tiktok_ui.py`
blocks any request from the browser to tiktok.com and confirms nothing was
posted.
