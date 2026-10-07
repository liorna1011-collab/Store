# Uploads – measured, and the production path

## What was measured (development container, 4 vCPU, no proxy)

| Path | Result | How |
|---|---|---|
| Polixor server ingest (byte ranges, SHA-256 checked, written to disk) | 180–470 MB/s (1.5–3.8 Gbps); best 16 MiB × 2 parallel | `PUT /api/uploads/{id}/range`, 256 MB per setting, chunk 4–32 MiB × 1–8 parallel |
| Browser (Chromium) → Polixor, 1 GB real video, whole flow incl. in-browser hashing and server verification | 6.4 s → 167 MB/s (1.3 Gbps) | the real New Project page |
| Same, 1 GiB, transfer only (first → last range) | 5.7 s → 188 MB/s | server telemetry |
| Browser main thread during a 1 GB upload | ~0.12 s busy per 4 s; no long task | Chrome CPU profile |
| Direct to object storage (S3 API, local moto server), 245 MB | 15 parts straight to storage; Polixor received 8 metadata requests and 0 video bytes | browser E2E |

**Conclusion.** The ~1 MB/s seen in the Codespace is not Polixor's own path: the same code
moves 167–188 MB/s end to end without the GitHub Codespaces port forwarder in between. The
forwarder could not be measured from the development container.
- **UNVERIFIED:** its exact ceiling. The admin view now shows it for every real upload (next
  section).
- **Practical consequence:** a 10 GB source through a Codespace is a development convenience,
  not the production ingest path.

## Where the time goes, for every real upload (Admin → Uploads)

Each upload records, per part of the path:

| Column | Meaning |
|---|---|
| MB/s · Mbps | end to end (MB/s = megabytes per second; Mbps = megabits per second = MB/s × 8) |
| browser hash MB/s | SHA-256 of the chunks in a Web Worker |
| request MB/s | bytes ÷ time each chunk request took, measured in the browser |
| server receive MB/s | first byte → last byte arriving at Polixor (network / proxy bound) |
| server write MB/s | disk writes on the server |

The slowest column is the bottleneck. If "request" and "server receive" are low while "hash"
and "write" are hundreds of MB/s, the network path (the Codespaces forwarder) is the limit.

## Development path (`local_resumable`, default)

* Byte-range PUTs, adaptive request size and parallelism. Behind the Codespaces forwarder the
  request size starts at 4 MiB and is capped at 8 MiB (bigger bodies get HTTP 413 there).
  Parallelism starts at 3 and grows to 6 only while the measured speed rises.
* SHA-256 per chunk in a Web Worker, checked by the server. There is no second whole-file
  read: completion only runs `ffprobe`. TLS already protects transit, so the per-chunk hash
  guards against proxy truncation or corruption and costs a few ms per chunk.
* Resume: choosing the same file again continues from the byte ranges the server has.

## Production path (`s3_multipart`)

The browser uploads **directly** to object storage. Polixor handles only:
- authentication;
- the quota check;
- creating the multipart upload;
- 15-minute pre-signed URLs, one per part;
- completion: it checks the stored size and probes the video through a signed GET URL.

The worker copies the object to its disk inside the cloud when processing starts.

```
POLIXOR_STORAGE=s3
POLIXOR_S3_BUCKET=polixor-sources
POLIXOR_S3_REGION=eu-central-1            # close to the customers; R2: auto
POLIXOR_S3_ENDPOINT=                      # empty for AWS; https://<account>.r2.cloudflarestorage.com for R2;
                                          # https://s3.<region>.backblazeb2.com for B2
POLIXOR_S3_PART_MB=64                     # grows automatically so a file fits in 10 000 parts
# keys (server side only): POLIXOR_S3_ACCESS_KEY_ID / POLIXOR_S3_SECRET_ACCESS_KEY, or the secret store
```

* **Bucket CORS:** allow `PUT` from the site's origin. ETags are read server side with
  ListParts, so exposing them is optional.
* **Resume:** the storage's own part list decides what is missing. This survives a refresh, a
  sleeping laptop, Wi-Fi loss and a sign-in renewal. Choosing the same file again continues
  the same multipart upload.
* **Secrets:** the browser receives part geometry and per-part signed URLs only, never a key
  (tested). Expired incomplete sessions abort their multipart upload, so stored parts stop
  costing money.
* **Speed:** limited by the customer's upstream and the distance to the bucket region.
  Polixor adds no cap. Parallelism adapts between 4 and 8 parts.
  - **ESTIMATED, not measured here:** tens of MB/s on a fast fibre line to a nearby region.

## Turn on direct-to-storage uploads (no code changes)

Put the settings in `~/.polixor/env` on the server (outside the repository, never committed);
`start.sh` loads it on every start. Then `bash polixor/scripts/codespaces/start.sh restart`.
Admin → Uploads → "Measure upload path" then shows `storage: s3_multipart`.

**Cloudflare R2** (no egress fees – the usual choice)
1. Cloudflare dashboard → R2 → Create bucket (e.g. `polixor-sources`).
2. R2 → Manage API tokens → Create token → *Object Read & Write*, scoped to that bucket. Copy
   the Access Key ID, the Secret Access Key and the account's S3 endpoint.
3. Bucket → Settings → CORS policy:
   ```json
   [{"AllowedOrigins": ["https://YOUR-POLIXOR-HOST"], "AllowedMethods": ["PUT", "GET", "HEAD"],
     "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]
   ```
4. `~/.polixor/env`:
   ```
   POLIXOR_STORAGE=s3
   POLIXOR_S3_BUCKET=polixor-sources
   POLIXOR_S3_REGION=auto
   POLIXOR_S3_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   POLIXOR_S3_ACCESS_KEY_ID=...
   POLIXOR_S3_SECRET_ACCESS_KEY=...
   ```

**AWS S3**
1. S3 → Create bucket in the region closest to your customers (e.g. `eu-central-1`), Block
   Public Access ON.
2. IAM → user (or role) with a policy limited to the bucket: `s3:PutObject`, `s3:GetObject`,
   `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts`, `s3:ListBucketMultipartUploads`.
3. Bucket → Permissions → CORS: the JSON above.
4. `~/.polixor/env`: as for R2 but `POLIXOR_S3_REGION=eu-central-1` and no `POLIXOR_S3_ENDPOINT`.

Keys stay on the server; the browser only ever receives per-part signed URLs (15 minutes).
Remove `POLIXOR_STORAGE` (or set it to `local`) to go back to the development path.

## What the customer sees

* An upload tray on every page: name, `x GB / y GB`, %, one speed in MB/s, and a rounded time
  left. The time left is a smoothed rolling window and is shown only after the first ~10 s.
* Pause, resume and cancel.
* **Start** can be pressed while the file is still uploading. The project starts by itself
  when the upload is verified, and the person can use the rest of the site meanwhile. The tab
  must stay open, because the bytes come from this browser.

## Measure your own path (admin → "Measure upload path", ~2 minutes)

Runs in YOUR browser over YOUR connection and proxy (e.g. the Codespaces forwarder): generated
test data goes first to a raw sink (`PUT /api/admin/upload-bench/sink`, read and dropped: the
ceiling of the line + proxy), then through the real Polixor range endpoint (worker hash, server
hash, disk write), over 4/8/16 MiB requests × 1–8 in parallel, 6–8 s each. The table shows MB/s
and Mbps per setting, the raw ceiling and the Polixor overhead. The best stable setting is saved
and every following upload starts from it (`transport.profile` ends with `+measured`).

## Uploading never blocks the app

Chrome opens at most 6 connections per host over HTTP/1.1. An upload with 6 requests in flight
made every click wait behind multi-second chunk requests (MEASURED before: API p95 1.7 s, a page
change 2.5 s; after: p95 68–157 ms). Over HTTP/1.1 the upload now keeps at most 4 requests in
flight; over HTTP/2/3 (one multiplexed connection) there is no such cap. Direct-to-storage
uploads go to another host and never compete with the app. Test:
`tests/e2e_upload_responsiveness.py`.
