from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eduplus_tools.monitor.service import StatusMonitor
from eduplus_tools.monitor.store import StatusStore
from eduplus_tools.notify.clients import CursorExpiredError, StatusApiClient, StatusApiError, TelegramClient, TelegramError
from eduplus_tools.web import server
from eduplus_tools.web.status_api import StatusApi

from status_fixtures import FakeClock, FakeUpstream, task


def serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture()
def status_server(monkeypatch) -> Iterator[tuple[str, FakeUpstream, StatusMonitor]]:
    upstream = FakeUpstream(tasks=[task("homework:a")])
    monitor = StatusMonitor(StatusStore(":memory:"), upstream, clock=FakeClock())
    monkeypatch.setattr(server, "STATUS_API", StatusApi(monitor, "secret"))
    httpd, base = serve(server.WebHandler)
    try:
        yield base, upstream, monitor
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_status_client_against_real_api(status_server) -> None:
    base, upstream, monitor = status_server
    client = StatusApiClient(base, "secret")
    assert client.latest_scan() is None

    monitor.scan_now()
    upstream.tasks.append(task("homework:b"))
    monitor.scan_now()

    page = client.events(after=None)
    assert [event["type"] for event in page["items"]] == ["task.published"]
    assert client.events(after=page["next_cursor"])["items"] == []
    assert [item["id"] for item in client.tasks(state="not_submitted")["items"]] == ["homework:a", "homework:b"]
    assert client.session()["valid"] is True
    assert client.latest_scan()["status"] == "succeeded"


def test_status_client_errors(status_server) -> None:
    base, _, monitor = status_server
    with pytest.raises(StatusApiError) as wrong_token:
        StatusApiClient(base, "wrong").session()
    assert (wrong_token.value.status, wrong_token.value.code) == (401, "unauthorized")

    monitor.scan_now()
    original = monitor.store.list_events

    def expired(*args, **kwargs):
        from eduplus_tools.monitor.store import CursorExpiredError as StoreCursorExpired

        raise StoreCursorExpired

    monitor.store.list_events = expired  # type: ignore[method-assign]
    try:
        with pytest.raises(CursorExpiredError):
            StatusApiClient(base, "secret").events(after="1")
    finally:
        monitor.store.list_events = original  # type: ignore[method-assign]


class FakeTelegramApi(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict]] = []

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeTelegramApi.calls.append((self.path, body))
        if self.path.endswith("/sendMessage") and body["text"] == "fail":
            self._reply(400, {"ok": False, "description": "Bad Request: message is too long"})
        elif self.path.endswith("/getUpdates"):
            self._reply(200, {"ok": True, "result": [{"update_id": 3, "message": {"chat": {"id": 1}, "text": "/todo"}}]})
        else:
            self._reply(200, {"ok": True, "result": {"message_id": 1}})

    def _reply(self, status: int, payload: dict) -> None:
        content = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_telegram_client() -> None:
    FakeTelegramApi.calls = []
    httpd, base = serve(FakeTelegramApi)
    try:
        client = TelegramClient("123:SECRET", api_base=base)
        client.send_message("1001", "<b>hi</b>")
        assert client.get_updates(offset=3, timeout=0)[0]["update_id"] == 3
        with pytest.raises(TelegramError) as error:
            client.send_message("1001", "fail")
    finally:
        httpd.shutdown()
        httpd.server_close()

    path, body = FakeTelegramApi.calls[0]
    assert path == "/bot123:SECRET/sendMessage"
    assert body == {"chat_id": "1001", "text": "<b>hi</b>", "parse_mode": "HTML", "disable_web_page_preview": True}
    assert FakeTelegramApi.calls[1][1]["offset"] == 3
    assert "message is too long" in str(error.value)
    assert "SECRET" not in str(error.value)


def test_telegram_network_error_hides_token() -> None:
    client = TelegramClient("123:SECRET", api_base="http://127.0.0.1:9", timeout=2)
    with pytest.raises(TelegramError) as error:
        client.send_message("1", "x")
    assert "SECRET" not in str(error.value)
