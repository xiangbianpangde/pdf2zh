"""Minimal HTTP client: explicit User-Agent, bounded retries, no third-party deps.

Two behaviours are load-bearing and must not be "cleaned up":

1. Every request sends an explicit ``User-Agent``. The default ``Python-urllib/x``
   string is refused by some provider front ends (Cloudflare answers 403 with
   ``error code: 1010``).
2. :func:`upload` sends nothing but the body. MinerU hands out pre-signed OSS URLs;
   extra headers can break the signature and yield 403.
"""
from __future__ import annotations

import http.client
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = ["Response", "request", "upload"]

USER_AGENT = "pdf2md/0.2 (+https://mineru.net)"
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> dict:
        try:
            payload = json.loads(self.body)
        except ValueError as exc:
            raise ValueError(f"响应不是 JSON（HTTP {self.status}）：{self.text[:200]}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"响应 JSON 不是对象（HTTP {self.status}）")
        return payload


def request(url: str, *, method: str = "GET", json_body: object | None = None,
            headers: dict[str, str] | None = None, timeout: float = 120.0,
            retries: int = 3, backoff: float = 2.0) -> Response:
    """Perform one HTTP call, retrying transport errors and retryable statuses."""
    data = None
    merged = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        merged["Content-Type"] = "application/json"
    for key, value in (headers or {}).items():
        merged[key] = value

    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        prepared = urllib.request.Request(url, data=data, method=method, headers=merged)
        try:
            with urllib.request.urlopen(prepared, timeout=timeout) as response:
                return Response(response.status, response.headers.get("Content-Type", ""),
                                response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code not in RETRY_STATUS or attempt == retries:
                return Response(exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", body)
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt == retries:
                break
        time.sleep(backoff * attempt + random.uniform(0, 0.5))
    raise ConnectionError(f"请求失败（重试 {retries} 次）：{url}：{last_error}")


def upload(url: str, payload: bytes, *, timeout: float = 300.0, attempts: int = 3) -> int:
    """PUT raw bytes to a pre-signed URL with no extra request headers.

    Returns the HTTP status. Raises ConnectionError when every attempt failed at
    the transport level.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"上传地址不合法：{url[:120]}")
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    last_error: Exception | None = None
    status = 0
    for attempt in range(1, max(1, attempts) + 1):
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=timeout)
        try:
            connection.request("PUT", path, body=payload)
            response = connection.getresponse()
            response.read()
            status = response.status
            if status in {200, 201}:
                return status
        except (OSError, http.client.HTTPException) as exc:
            last_error = exc
        finally:
            connection.close()
        if attempt < attempts:
            time.sleep(2 * attempt)
    if last_error is not None:
        raise ConnectionError(f"上传失败：{last_error}")
    return status
