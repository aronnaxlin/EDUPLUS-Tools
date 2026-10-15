from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class StatusApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class CursorExpiredError(StatusApiError):
    pass


class TelegramError(RuntimeError):
    pass


class StatusApiClient:
    """Client for this project's /api/v1 status API (docs/openapi/status-api.yaml)."""

    def __init__(self, base_url: str, token: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def events(self, after: str | None, limit: int = 50) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if after is not None:
            params["after"] = after
        try:
            return self._request("GET", "/api/v1/events", params)
        except StatusApiError as exc:
            if exc.status == 410:
                raise CursorExpiredError(str(exc), exc.status, exc.code) from None
            raise

    def tasks(self, **params: Any) -> dict[str, Any]:
        return self._request("GET", "/api/v1/tasks", params)

    def session(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/session")

    def latest_scan(self) -> dict[str, Any] | None:
        try:
            return self._request("GET", "/api/v1/scans/latest")
        except StatusApiError as exc:
            if exc.status == 404:
                return None
            raise

    def start_scan(self) -> dict[str, Any]:
        return self._request("POST", "/api/v1/scans")

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(
            url,
            method=method,
            data=b"" if method == "POST" else None,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except (OSError, ValueError):
                body = {}
            message = body.get("error") if isinstance(body, dict) else None
            code = body.get("code") if isinstance(body, dict) else None
            raise StatusApiError(f"状态 API {path} 返回 {exc.code}：{message or exc.reason}", exc.code, code) from None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise StatusApiError(f"状态 API {path} 请求失败：{exc}") from None


class TelegramClient:
    """Minimal Telegram Bot API client. Never logs request URLs, because they contain the bot token."""

    def __init__(self, token: str, api_base: str = "https://api.telegram.org", timeout: int = 30) -> None:
        self._url = f"{api_base.rstrip('/')}/bot{token}/"
        self.timeout = timeout

    def send_message(self, chat_id: str, text: str) -> None:
        self._call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        )

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout + 10)
        return result if isinstance(result, list) else []

    def _call(self, method: str, payload: dict[str, Any], timeout: int | None = None) -> Any:
        request = urllib.request.Request(
            self._url + method,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                description = json.loads(exc.read().decode("utf-8")).get("description")
            except (OSError, ValueError, AttributeError):
                description = None
            raise TelegramError(f"Telegram {method} 返回 {exc.code}：{description or exc.reason}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise TelegramError(f"Telegram {method} 请求失败：{type(exc).__name__}") from None
        if not isinstance(body, dict) or not body.get("ok"):
            raise TelegramError(f"Telegram {method} 失败：{body.get('description') if isinstance(body, dict) else body}")
        return body.get("result")
