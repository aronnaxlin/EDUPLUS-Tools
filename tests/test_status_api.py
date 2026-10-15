from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer

import pytest

from eduplus_tools.monitor.service import StatusMonitor
from eduplus_tools.monitor.store import StatusStore
from eduplus_tools.web import server
from eduplus_tools.web.status_api import StatusApi

from status_fixtures import FakeClock, FakeUpstream, course, task


TOKEN = "test-token"


@pytest.fixture()
def api(monkeypatch) -> Iterator[tuple[str, FakeUpstream, StatusMonitor]]:
    upstream = FakeUpstream(
        courses=[course("c1"), course("old", end="2026-01-31T23:59:59")],
        tasks=[
            task("homework:due-soon", due_at="2026-10-15T23:59:59+08:00"),
            task("homework:later", state="submitted", due_at="2026-10-31T23:59:59+08:00"),
            task("homework:late", due_at="2026-10-12T14:30:59+08:00"),
            task("experiment:no-due", due_at=None),
            task("homework:old-course", course_id="old"),
        ],
    )
    monitor = StatusMonitor(StatusStore(":memory:"), upstream, clock=FakeClock())
    monkeypatch.setattr(server, "STATUS_API", StatusApi(monitor, TOKEN))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.WebHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", upstream, monitor
    finally:
        httpd.shutdown()
        httpd.server_close()


def call(base: str, path: str, method: str = "GET", token: str | None = TOKEN) -> tuple[int, dict, dict]:
    request = urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read()), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), dict(exc.headers)


def wait_for_scan(monitor: StatusMonitor, scan_id: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        latest = monitor.latest_scan()
        if latest and latest["id"] == scan_id and latest["status"] != "running":
            return
        time.sleep(0.01)
    raise AssertionError("background scan did not finish")


def ids(body: dict) -> list[str]:
    return [item["id"] for item in body["items"]]


def test_requires_bearer_token(api) -> None:
    base, _, _ = api
    status, body, headers = call(base, "/api/v1/tasks", token=None)
    assert status == 401
    assert body["code"] == "unauthorized"
    assert headers["WWW-Authenticate"] == "Bearer"
    assert call(base, "/api/v1/tasks", token="wrong")[0] == 401


def test_no_snapshot_before_first_scan(api) -> None:
    base, _, _ = api
    status, body, _ = call(base, "/api/v1/tasks")
    assert (status, body["code"]) == (503, "no_snapshot")
    assert call(base, "/api/v1/scans/latest")[0] == 404


def test_tasks_filters_and_order(api) -> None:
    base, _, monitor = api
    monitor.scan_now()

    status, body, _ = call(base, "/api/v1/tasks")
    assert status == 200
    # Sorted by due_at, no due date last, inactive course excluded by default.
    assert ids(body) == ["homework:late", "homework:due-soon", "homework:later", "experiment:no-due"]
    assert body["partial_failures"] == []

    assert ids(call(base, "/api/v1/tasks?state=not_submitted&type=homework")[1]) == ["homework:late", "homework:due-soon"]
    assert ids(call(base, "/api/v1/tasks?overdue=true")[1]) == ["homework:late"]
    assert ids(call(base, "/api/v1/tasks?due_before=2026-10-16T00:00:00%2B08:00&due_after=2026-10-15T00:00:00+08:00")[1]) == ["homework:due-soon"]
    assert "homework:old-course" in ids(call(base, "/api/v1/tasks?active_courses_only=false")[1])
    assert ids(call(base, "/api/v1/tasks?course_id=old&active_courses_only=false")[1]) == ["homework:old-course"]


def test_tasks_rejects_bad_parameters(api) -> None:
    base, _, monitor = api
    monitor.scan_now()
    for query in ("state=done", "overdue=maybe", "due_before=tomorrow"):
        status, body, _ = call(base, f"/api/v1/tasks?{query}")
        assert (status, body["code"]) == (400, "invalid_parameter"), query


def test_task_detail_and_courses(api) -> None:
    base, _, monitor = api
    monitor.scan_now()
    status, body, _ = call(base, "/api/v1/tasks/homework%3Alate")
    assert status == 200
    assert body["overdue"] is True
    assert body["course"] == {"id": "c1", "name": "课程c1"}
    assert call(base, "/api/v1/tasks/homework:missing")[0] == 404

    assert ids(call(base, "/api/v1/courses")[1]) == ["c1"]
    assert ids(call(base, "/api/v1/courses?active=false")[1]) == ["c1", "old"]


def test_scan_and_events_flow(api) -> None:
    base, upstream, monitor = api
    status, scan, _ = call(base, "/api/v1/scans", method="POST")
    assert status == 202
    assert scan["status"] in {"running", "succeeded"}
    wait_for_scan(monitor, scan["id"])

    upstream.tasks.append(task("homework:new"))
    monitor.scan_now()
    status, body, _ = call(base, "/api/v1/events")
    assert status == 200
    assert [item["type"] for item in body["items"]] == ["task.published"]
    assert body["items"][0]["task"]["id"] == "homework:new"
    cursor = body["next_cursor"]

    status, body, _ = call(base, f"/api/v1/events?after={cursor}")
    assert body == {"items": [], "next_cursor": cursor}
    assert call(base, "/api/v1/events?after=abc")[0] == 400

    status, latest, _ = call(base, "/api/v1/scans/latest")
    assert status == 200 and latest["status"] == "succeeded"


def test_session_endpoint(api) -> None:
    base, upstream, _ = api
    status, body, _ = call(base, "/api/v1/session")
    assert status == 200 and body["valid"] is True and body["upstream_code"] == 2000000
    upstream.session_valid = False
    status, body, _ = call(base, "/api/v1/session")
    assert status == 200 and body["valid"] is False and body["upstream_code"] == 4010000
    upstream.broken = True
    status, body, _ = call(base, "/api/v1/session")
    assert (status, body["code"]) == (502, "upstream_unavailable")


def test_disabled_without_token(monkeypatch) -> None:
    monkeypatch.setattr(server, "STATUS_API", None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.WebHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, body, _ = call(f"http://127.0.0.1:{httpd.server_address[1]}", "/api/v1/tasks")
        assert (status, body["code"]) == (404, "not_found")
    finally:
        httpd.shutdown()
        httpd.server_close()
