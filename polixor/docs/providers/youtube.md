# YouTube — provider notes (Stage 7)

Documentation checked on 2026-09-30, while implementing
`backend/polixor/services/publishing/youtube.py`.

`developers.google.com` is blocked from the development environment, so the
Google pages were read through search results that quote them. Re-check this
list against the live pages before the first real upload.

| Topic | What the implementation does | Source |
|---|---|---|
| OAuth | Authorization code with PKCE (S256), `access_type=offline`, `prompt=consent`. Scopes: `youtube.upload` and `youtube.readonly`. Connecting fails if Google returns no refresh token or doesn't grant the upload scope. | Google OAuth 2.0 for web server apps |
| Token and revoke | `oauth2.googleapis.com/token` (code, refresh); `oauth2.googleapis.com/revoke` when disconnecting. | same |
| Upload | Resumable: `POST upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`, then chunked `PUT`s with `Content-Range`. A `308` reply's `Range` header gives the next offset. After an interruption Polixor sends `Content-Range: bytes */N` and resumes from the byte the server reports. | [Resumable uploads guide](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol?hl=en) |
| Unverified projects | Uploads from API projects created after 28 July 2020 that haven't passed Google's audit are **private**. Polixor shows this as a warning until *Settings → Publishing → "My YouTube API project passed Google's audit"* is turned on. | [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert?authuser=1) |
| Quota | **Contradicts the approved plan**, which counted `videos.insert` as 100 units of the shared 10,000-unit daily pool. Since **1 June 2026**, `videos.insert` and `search.list` each have their own quota bucket. `quotaExceeded` / `uploadLimitExceeded` errors become a clear "posting limit" message without automatic retries. | [Revision history](https://developers.google.com/youtube/v3/revision_history) |
| Scheduling | `status.publishAt` only works with `privacyStatus=private`; Polixor forces private whenever a time is set. YouTube publishes by itself, so Polixor doesn't need to be running. | videos resource / insert |
| Disclosures | `status.selfDeclaredMadeForKids` is always sent (default false; there's a checkbox). `status.containsSyntheticMedia` is sent when the user ticks the AI-content box (supported since 30 Oct 2024). | [Revision history](https://developers.google.com/youtube/v3/revision_history) |
| Shorts | There's no separate API flag; YouTube classifies vertical or square videos up to 3 minutes as Shorts. Polixor blocks a horizontal "short". | YouTube Help |
| Thumbnails | A separate `thumbnails.set` call, only for long videos, only when the file is under 2 MB. A failure (for example on an unverified channel) doesn't fail the post. | videos.insert / thumbnails.set |
| Long videos | Uploads over 15 minutes need a verified channel. Polixor shows a warning, not a block. | YouTube Help |

## Not verified here

- No real upload was made. Every test runs against a mocked Google server
  (`tests/test_youtube.py`), which fails on any unexpected request.
- The 2 MB thumbnail limit and "made for kids is required" come from
  secondary sources. The implementation is safe either way: the flag is always
  sent, and oversized thumbnails are skipped.
