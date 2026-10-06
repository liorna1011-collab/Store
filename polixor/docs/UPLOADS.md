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

## What the customer sees

* An upload tray on every page: name, `x GB / y GB`, %, one speed in MB/s, and a rounded time
  left. The time left is a smoothed rolling window and is shown only after the first ~10 s.
* Pause, resume and cancel.
* **Start** can be pressed while the file is still uploading. The project starts by itself
  when the upload is verified, and the person can use the rest of the site meanwhile. The tab
  must stay open, because the bytes come from this browser.
