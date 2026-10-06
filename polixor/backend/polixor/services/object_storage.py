"""
Where uploaded sources are stored, and how the browser gets the bytes there.

  local_resumable  (default, development, Codespaces)  the browser sends byte ranges to Polixor
                   (services/uploads.py); the file lands in <data>/uploads → <data>/sources
  s3_multipart     (production)  the browser uploads DIRECTLY to S3-compatible object storage
                   (AWS S3, Cloudflare R2, Backblaze B2, MinIO …) with an S3 multipart upload: Polixor
                   only authenticates, checks the quota, creates the multipart upload, hands out
                   short-lived pre-signed URLs per part, and completes + verifies the object. No video
                   byte passes through the Polixor application server.

Configuration (server side only – the browser never receives a key):

  POLIXOR_STORAGE=s3                     choose the provider (default: local)
  POLIXOR_S3_BUCKET=polixor-sources      bucket
  POLIXOR_S3_REGION=eu-central-1         the region close to the customers (R2: "auto")
  POLIXOR_S3_ENDPOINT=https://<acct>.r2.cloudflarestorage.com   (R2 / B2 / MinIO; empty for AWS)
  POLIXOR_S3_PREFIX=sources/             object key prefix
  POLIXOR_S3_PART_MB=64                  part size (5 MiB … 5 GiB; 10 000 parts at most)
  secrets: s3_access_key_id, s3_secret_access_key (settings → secrets, or POLIXOR_S3_ACCESS_KEY_ID /
           POLIXOR_S3_SECRET_ACCESS_KEY)

The bucket needs a CORS rule that allows PUT from the site's origin and EXPOSES the ETag header
(the browser needs each part's ETag to complete the upload) – see docs/UPLOADS.md.

Resume: the multipart upload id, the object key, the part size and the file fingerprint are kept
in the upload session; on return the browser asks which parts the storage already has (ListParts)
and sends only the missing ones – across refreshes, sleep, Wi-Fi loss and sign-in renewals.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

MIB = 1024 * 1024
MIN_PART = 5 * MIB
MAX_PARTS = 10_000
URL_TTL = 15 * 60                # a pre-signed part URL lives 15 minutes


def provider_name() -> str:
    v = os.environ.get("POLIXOR_STORAGE", "local").strip().lower()
    return "s3" if v in ("s3", "r2", "b2", "s3_multipart") else "local"


@dataclass
class S3Config:
    bucket: str
    region: str
    endpoint: str
    prefix: str
    part_bytes: int
    access_key: str
    secret_key: str

    @property
    def label(self) -> str:
        e = self.endpoint.lower()
        return "r2" if "r2.cloudflarestorage" in e else "b2" if "backblaze" in e else "s3"


def s3_config() -> Optional[S3Config]:
    from ..config import SECRETS

    bucket = os.environ.get("POLIXOR_S3_BUCKET", "").strip()
    ak = os.environ.get("POLIXOR_S3_ACCESS_KEY_ID") or SECRETS.get("s3_access_key_id") or ""
    sk = os.environ.get("POLIXOR_S3_SECRET_ACCESS_KEY") or SECRETS.get("s3_secret_access_key") or ""
    if not bucket or not ak or not sk:
        return None
    try:
        part = int(float(os.environ.get("POLIXOR_S3_PART_MB", "64")) * MIB)
    except ValueError:
        part = 64 * MIB
    return S3Config(bucket=bucket, region=os.environ.get("POLIXOR_S3_REGION", "us-east-1").strip() or "us-east-1",
                    endpoint=os.environ.get("POLIXOR_S3_ENDPOINT", "").strip(),
                    prefix=os.environ.get("POLIXOR_S3_PREFIX", "sources/"), part_bytes=max(MIN_PART, part),
                    access_key=ak, secret_key=sk)


def _client(cfg: S3Config):
    import boto3
    from botocore.config import Config

    return boto3.client("s3", region_name=cfg.region, endpoint_url=cfg.endpoint or None,
                        aws_access_key_id=cfg.access_key, aws_secret_access_key=cfg.secret_key,
                        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}))


def part_size_for(size: int, preferred: int) -> int:
    """At least `preferred`, large enough that the file fits in MAX_PARTS parts."""
    need = math.ceil(size / MAX_PARTS)
    return max(MIN_PART, preferred, need)


class S3Multipart:
    """The storage side of a direct upload. Every method is a short metadata call – no video bytes."""

    def __init__(self, cfg: Optional[S3Config] = None) -> None:
        self.cfg = cfg or s3_config()
        if self.cfg is None:
            raise RuntimeError("object storage is not configured (POLIXOR_S3_BUCKET + keys)")
        self.s3 = _client(self.cfg)

    def create(self, filename: str, size: int, content_type: str = "video/mp4") -> dict[str, Any]:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in filename)[-120:] or "source"
        key = f"{self.cfg.prefix}{time.strftime('%Y/%m/%d')}/{os.urandom(8).hex()}-{safe}"
        r = self.s3.create_multipart_upload(Bucket=self.cfg.bucket, Key=key, ContentType=content_type)
        part = part_size_for(size, self.cfg.part_bytes)
        return {"key": key, "multipart_id": r["UploadId"], "part_bytes": part,
                "parts": max(1, math.ceil(size / part)), "provider": self.cfg.label, "region": self.cfg.region}

    def sign_parts(self, key: str, multipart_id: str, numbers: list[int]) -> dict[int, str]:
        return {n: self.s3.generate_presigned_url(
            "upload_part", Params={"Bucket": self.cfg.bucket, "Key": key, "UploadId": multipart_id,
                                   "PartNumber": int(n)}, ExpiresIn=URL_TTL, HttpMethod="PUT")
            for n in numbers}

    def list_parts(self, key: str, multipart_id: str) -> list[dict[str, Any]]:
        """The parts the storage already has (resume asks this, not the browser's memory)."""
        out, marker = [], 0
        while True:
            r = self.s3.list_parts(Bucket=self.cfg.bucket, Key=key, UploadId=multipart_id,
                                   PartNumberMarker=marker, MaxParts=1000)
            out += [{"part_number": p["PartNumber"], "etag": p["ETag"], "size": p["Size"]} for p in r.get("Parts", [])]
            if not r.get("IsTruncated"):
                return out
            marker = r["NextPartNumberMarker"]

    def complete(self, key: str, multipart_id: str, parts: list[dict[str, Any]], expected_size: int) -> dict[str, Any]:
        stored = {p["part_number"]: p for p in self.list_parts(key, multipart_id)}
        if len(stored) < len({int(p["part_number"]) for p in parts}):
            raise ValueError("parts missing in storage")
        use = [{"PartNumber": n, "ETag": stored[n]["etag"]} for n in sorted(stored)]
        self.s3.complete_multipart_upload(Bucket=self.cfg.bucket, Key=key, UploadId=multipart_id,
                                          MultipartUpload={"Parts": use})
        head = self.s3.head_object(Bucket=self.cfg.bucket, Key=key)
        if int(head["ContentLength"]) != int(expected_size):
            raise ValueError(f"stored size {head['ContentLength']} != expected {expected_size}")
        return {"key": key, "size": int(head["ContentLength"]), "etag": head.get("ETag", "")}

    def abort(self, key: str, multipart_id: str) -> None:
        try:
            self.s3.abort_multipart_upload(Bucket=self.cfg.bucket, Key=key, UploadId=multipart_id)
        except Exception:                                    # noqa: BLE001 – already gone
            pass

    def read_url(self, key: str, ttl: int = 6 * 3600) -> str:
        """A pre-signed GET – FFmpeg / ffprobe read the object with range requests (no full download)."""
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.cfg.bucket, "Key": key},
                                              ExpiresIn=ttl)

    def download(self, key: str, dest: str) -> None:
        """Server-side copy for processing (inside the cloud: storage → worker, not through the customer)."""
        self.s3.download_file(self.cfg.bucket, key, dest)


def describe() -> dict[str, Any]:
    """Admin view: which provider, region, bucket (never the keys)."""
    if provider_name() != "s3":
        return {"provider": "local_resumable"}
    cfg = s3_config()
    if cfg is None:
        return {"provider": "s3_multipart", "configured": False}
    return {"provider": f"{cfg.label}_multipart", "configured": True, "bucket": cfg.bucket, "region": cfg.region,
            "endpoint": cfg.endpoint or "aws", "part_mb": cfg.part_bytes // MIB}


__all__ = ["provider_name", "s3_config", "S3Multipart", "describe", "part_size_for"]
