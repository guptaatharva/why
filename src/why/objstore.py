"""Presigned S3 URLs (AWS Signature V4) for Nebius Object Storage, without boto3.

Nebius Serverless Jobs take at most 64 KiB through `--inject-file`. Bigger sandboxes go to a
bucket instead: Why PUTs the tarball with a presigned URL, the job downloads it with a
short-lived presigned GET, and Why deletes it afterwards. The job never sees the storage keys.

Config (`.why/config.yaml`):
    nebius:
      bucket: why-sandboxes
      endpoint: https://storage.eu-north1.nebius.cloud
      region: eu-north1
Keys: NEBIUS_S3_ACCESS_KEY_ID / NEBIUS_S3_SECRET_ACCESS_KEY (or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx

DEFAULT_ENDPOINT = "https://storage.eu-north1.nebius.cloud"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def presign(method: str, url: str, region: str, access_key: str, secret_key: str, expires: int = 3600,
            when: datetime | None = None, service: str = "s3") -> str:
    """Query-string-authenticated URL (SigV4, UNSIGNED-PAYLOAD)."""
    when = when or datetime.now(timezone.utc)
    amz_date = when.strftime("%Y%m%dT%H%M%SZ")
    date = when.strftime("%Y%m%d")
    parts = urlsplit(url)
    host = parts.netloc
    path = quote(parts.path or "/", safe="/~")
    scope = f"{date}/{region}/{service}/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    query = "&".join(f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted(params.items()))
    canonical = "\n".join([method, path, query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(f"AWS4{secret_key}".encode(), date), region), service), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{parts.scheme}://{host}{path}?{query}&X-Amz-Signature={sig}"


class Bucket:
    def __init__(self, bucket: str, endpoint: str = DEFAULT_ENDPOINT, region: str = "eu-north1",
                 access_key: str | None = None, secret_key: str | None = None):
        self.bucket = bucket
        self.endpoint = endpoint.rstrip("/")
        self.region = region
        self.access_key = access_key or os.environ.get("NEBIUS_S3_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID")
        self.secret_key = (secret_key or os.environ.get("NEBIUS_S3_SECRET_ACCESS_KEY")
                           or os.environ.get("AWS_SECRET_ACCESS_KEY"))
        if not (self.access_key and self.secret_key):
            raise RuntimeError("object storage: set NEBIUS_S3_ACCESS_KEY_ID and NEBIUS_S3_SECRET_ACCESS_KEY")

    @classmethod
    def from_config(cls, cfg: dict) -> "Bucket | None":
        if not cfg.get("bucket"):
            return None
        return cls(cfg["bucket"], cfg.get("endpoint", DEFAULT_ENDPOINT), cfg.get("region", "eu-north1"))

    def url(self, method: str, key: str, expires: int = 3600) -> str:
        assert self.access_key and self.secret_key
        return presign(method, f"{self.endpoint}/{self.bucket}/{key}", self.region, self.access_key,
                       self.secret_key, expires)

    def put(self, key: str, data: bytes) -> None:
        r = httpx.put(self.url("PUT", key, 900), content=data, timeout=120)
        if r.status_code >= 300:
            raise RuntimeError(f"object storage PUT {key}: {r.status_code} {r.text[:300]}")

    def delete(self, key: str) -> None:
        try:
            httpx.delete(self.url("DELETE", key, 300), timeout=30)
        except httpx.HTTPError:
            pass  # a lifecycle rule on the bucket cleans up stragglers
