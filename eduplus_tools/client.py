from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from .config import EduplusConfig, HM_LVT_COOKIE_NAME


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


class EduplusClient:
    def __init__(self, config: EduplusConfig, verbose: bool = False) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.timeout = config.timeout
        self.verbose = verbose

    def api_json(self, path: str, referer: str | None = None) -> dict[str, Any]:
        url = self.base_url + path
        if self.verbose:
            print(f"GET {url}")
        request = urllib.request.Request(url, headers=self.headers(referer=referer, accept_json=True))
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read()
        try:
            data = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Non-JSON response from {url}: {payload[:200]!r}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected JSON response from {url}: {type(data).__name__}")
        return data

    def download_bytes(self, url: str, referer: str | None = None) -> bytes:
        if self.verbose:
            print(f"DOWNLOAD {url}")
        request = urllib.request.Request(url, headers=self.headers(referer=referer, accept_json=False))
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def headers(self, referer: str | None = None, accept_json: bool = True) -> dict[str, str]:
        cookie_parts = [f"SESSION={self.config.session}"]
        if self.config.hm_lvt:
            cookie_parts.append(f"{HM_LVT_COOKIE_NAME}={self.config.hm_lvt}")
            cookie_parts.append(f"hm_lvt={self.config.hm_lvt}")

        return {
            "Accept": "application/json, text/plain, */*" if accept_json else "*/*",
            "Cookie": "; ".join(cookie_parts),
            "Referer": referer or f"{self.base_url}/student/courses",
            "User-Agent": USER_AGENT,
            "x-access-token": self.config.session,
        }


def course_referer(base_url: str, course_id: str) -> str:
    return f"{base_url.rstrip('/')}/course/courseWarePreview/{urllib.parse.quote(course_id)}?userRole="
