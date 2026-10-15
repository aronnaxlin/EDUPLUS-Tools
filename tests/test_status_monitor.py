from __future__ import annotations

import pytest

from eduplus_tools.features.status import ScanFailure, SessionExpiredError
from eduplus_tools.monitor import store as store_module
from eduplus_tools.monitor.diff import diff_tasks
from eduplus_tools.monitor.service import EduplusUpstream, StatusMonitor
from eduplus_tools.monitor.store import CursorExpiredError, StatusStore

from status_fixtures import FakeClock, FakeUpstream, task


def event_types(monitor: StatusMonitor) -> list[str]:
    items, _ = monitor.store.list_events(None, 200)
    return [item["type"] for item in items]


def make_monitor(upstream: FakeUpstream, clock: FakeClock | None = None) -> StatusMonitor:
    return StatusMonitor(StatusStore(":memory:"), upstream, clock=clock or FakeClock())


def test_diff_baseline_emits_nothing() -> None:
    assert diff_tasks(None, {"homework:a": task("homework:a")}) == []


def test_diff_state_transitions() -> None:
    before = {
        "homework:a": task("homework:a", state="not_submitted"),
        "homework:b": task("homework:b", state="submitted"),
        "homework:c": task("homework:c", state="submitted"),
    }
    after = {
        "homework:a": task("homework:a", state="submitted"),
        "homework:b": task("homework:b", state="graded"),
        "homework:c": task("homework:c", state="not_submitted"),
    }
    events = {event.task.id: event for event in diff_tasks(before, after) if event.task}
    assert events["homework:a"].type == "task.submitted"
    assert events["homework:b"].type == "task.graded"
    assert events["homework:c"].type == "task.withdrawn"
    assert events["homework:c"].changes == {"state": {"from": "submitted", "to": "not_submitted"}}


def test_diff_published_due_changed_and_removed() -> None:
    before = {"homework:old": task("homework:old"), "homework:due": task("homework:due")}
    after = {"homework:due": task("homework:due", due_at="2026-10-25T23:59:59+08:00"), "homework:new": task("homework:new")}
    types = sorted(event.type for event in diff_tasks(before, after))
    assert types == ["task.due_changed", "task.published", "task.removed"]


def test_monitor_first_scan_is_baseline_then_detects_new_task() -> None:
    upstream = FakeUpstream(tasks=[task("homework:a")])
    clock = FakeClock()
    monitor = make_monitor(upstream, clock)

    first = monitor.scan_now()
    assert first["status"] == "succeeded"
    assert first["event_count"] == 0
    assert monitor.snapshot() is not None

    upstream.tasks.append(task("homework:b"))
    clock.value = clock.value.replace(hour=13)
    monitor.scan_now()
    assert event_types(monitor) == ["task.published"]

    snapshot = monitor.snapshot()
    assert snapshot is not None
    assert snapshot.tasks["homework:a"].first_seen_at == "2026-10-15T12:00:00+08:00"
    assert snapshot.tasks["homework:b"].first_seen_at == "2026-10-15T13:00:00+08:00"


def test_monitor_keeps_tasks_of_failed_course_without_removed_event() -> None:
    upstream = FakeUpstream(tasks=[task("homework:a"), task("experiment:x")])
    monitor = make_monitor(upstream)
    monitor.scan_now()

    upstream.tasks = [task("experiment:x")]
    upstream.failures = [ScanFailure(course_id="c1", type="homework", error="超时")]
    scan = monitor.scan_now()

    assert scan["status"] == "partially_failed"
    assert scan["failures"] == [{"course_id": "c1", "type": "homework", "error": "超时"}]
    snapshot = monitor.snapshot()
    assert snapshot is not None and "homework:a" in snapshot.tasks
    assert event_types(monitor) == []


def test_monitor_session_events_fire_once_per_transition() -> None:
    upstream = FakeUpstream(tasks=[task("homework:a")])
    monitor = make_monitor(upstream)
    monitor.scan_now()

    upstream.session_valid = False
    failed = monitor.scan_now()
    monitor.scan_now()
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "upstream_session_expired"
    assert monitor.snapshot() is not None

    upstream.session_valid = True
    monitor.scan_now()
    assert event_types(monitor) == ["session.expired", "session.restored"]


def test_monitor_upstream_outage_keeps_snapshot() -> None:
    upstream = FakeUpstream(tasks=[task("homework:a")])
    monitor = make_monitor(upstream)
    monitor.scan_now()
    upstream.broken = True
    scan = monitor.scan_now()
    assert scan["status"] == "failed"
    assert scan["error"]["code"] == "upstream_unavailable"
    assert monitor.latest_scan()["id"] == scan["id"]
    assert event_types(monitor) == []


def test_missing_session_config_counts_as_expired_session() -> None:
    def load_client():
        raise SystemExit("Missing required config: session.")

    with pytest.raises(SessionExpiredError):
        EduplusUpstream(load_client).check_session()

    monitor = StatusMonitor(StatusStore(":memory:"), EduplusUpstream(load_client), clock=FakeClock())
    scan = monitor.scan_now()
    assert scan["error"]["code"] == "upstream_session_expired"
    assert event_types(monitor) == ["session.expired"]


def test_snapshot_survives_restart(tmp_path) -> None:
    db = tmp_path / "status.sqlite3"
    upstream = FakeUpstream(tasks=[task("homework:a")])
    StatusMonitor(StatusStore(db), upstream, clock=FakeClock()).scan_now()

    upstream.tasks.append(task("homework:b"))
    restarted = StatusMonitor(StatusStore(db), upstream, clock=FakeClock())
    restarted.scan_now()
    assert event_types(restarted) == ["task.published"]


def test_events_cursor_paging_and_expiry(monkeypatch) -> None:
    upstream = FakeUpstream(tasks=[])
    monitor = make_monitor(upstream)
    monitor.scan_now()
    for index in range(5):
        upstream.tasks.append(task(f"homework:{index}"))
        monitor.scan_now()

    store = monitor.store
    page, cursor = store.list_events(None, 2)
    assert [item["id"] for item in page] == ["1", "2"]
    rest, cursor = store.list_events(int(cursor), 10)
    assert [item["id"] for item in rest] == ["3", "4", "5"]
    empty, same = store.list_events(int(cursor), 10)
    assert empty == [] and same == cursor

    monkeypatch.setattr(store_module, "MAX_EVENTS", 2)
    upstream.tasks.append(task("homework:6"))
    monitor.scan_now()
    with pytest.raises(CursorExpiredError):
        store.list_events(1, 10)
