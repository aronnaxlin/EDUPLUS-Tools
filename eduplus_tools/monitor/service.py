from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Any, Protocol

from ..core.client import EduplusClient
from ..features.status import (
    CHINA_TZ,
    FetchResult,
    SessionCheck,
    SessionExpiredError,
    Task,
    UpstreamError,
    check_session,
    fetch_status,
)
from .diff import EventDraft, diff_tasks
from .store import Snapshot, StatusStore


class Upstream(Protocol):
    def check_session(self) -> SessionCheck: ...

    def fetch(self, teach_class_cache: dict[str, str]) -> FetchResult: ...


class EduplusUpstream:
    """Builds a fresh client for every call so an updated config.json takes effect without a restart."""

    def __init__(self, load_client: Callable[[], EduplusClient]) -> None:
        self._load_client = load_client

    def check_session(self) -> SessionCheck:
        return check_session(self._client())

    def fetch(self, teach_class_cache: dict[str, str]) -> FetchResult:
        return fetch_status(self._client(), teach_class_cache)

    def _client(self) -> EduplusClient:
        try:
            return self._load_client()
        except SystemExit as exc:
            raise SessionExpiredError(f"未配置 EDUPLUS 登录态：{exc}") from exc


def merge_tasks(previous: Snapshot | None, result: FetchResult, seen_at: str) -> dict[str, Task]:
    """Build the new task map, keeping last known tasks for course/type pairs that failed this scan."""
    tasks = {task.id: task for task in result.tasks}
    previous_tasks = previous.tasks if previous else {}
    failed = {(failure.course_id, failure.type) for failure in result.failures}
    for task in previous_tasks.values():
        if (task.course_id, task.type) in failed and task.id not in tasks:
            tasks[task.id] = task
    for task in tasks.values():
        old = previous_tasks.get(task.id)
        task.first_seen_at = old.first_seen_at if old else seen_at
    return tasks


class StatusMonitor:
    def __init__(
        self,
        store: StatusStore,
        upstream: Upstream,
        *,
        interval_seconds: int = 600,
        clock: Callable[[], datetime] = lambda: datetime.now(CHINA_TZ),
    ) -> None:
        self._store = store
        self._upstream = upstream
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._running: dict[str, Any] | None = None
        self._snapshot = store.load_snapshot()
        self._teach_class_cache: dict[str, str] = {}

    @property
    def store(self) -> StatusStore:
        return self._store

    def now(self) -> datetime:
        return self._clock()

    def snapshot(self) -> Snapshot | None:
        return self._snapshot

    def latest_scan(self) -> dict[str, Any] | None:
        with self._lock:
            if self._running is not None:
                return dict(self._running)
        return self._store.load_latest_scan()

    def check_session(self) -> SessionCheck:
        return self._upstream.check_session()

    def start_scan(self, trigger: str = "manual") -> dict[str, Any]:
        """Start a scan in the background, or return the one already running."""
        scan, created = self._claim(trigger)
        if created:
            threading.Thread(target=self._run, args=(scan,), daemon=True).start()
        return dict(scan)

    def scan_now(self, trigger: str = "manual") -> dict[str, Any]:
        """Run a scan in the calling thread, or return the one already running."""
        scan, created = self._claim(trigger)
        return self._run(scan) if created else dict(scan)

    def start_scheduler(self) -> None:
        threading.Thread(target=self._schedule_loop, daemon=True).start()

    def _schedule_loop(self) -> None:
        while True:
            try:
                self.scan_now("schedule")
            except Exception:  # keep the scheduler alive; the next round retries
                traceback.print_exc()
            time.sleep(self._interval_seconds)

    def _claim(self, trigger: str) -> tuple[dict[str, Any], bool]:
        with self._lock:
            if self._running is not None:
                return self._running, False
            self._running = {
                "id": uuid.uuid4().hex[:12],
                "status": "running",
                "trigger": trigger,
                "started_at": self._iso_now(),
                "finished_at": None,
                "task_count": None,
                "event_count": None,
                "failures": [],
            }
            return self._running, True

    def _run(self, running: dict[str, Any]) -> dict[str, Any]:
        try:
            # Work on a copy so readers never see a finished status while the scan still counts as running.
            return self._scan(dict(running))
        except BaseException:
            with self._lock:
                self._running = None
            raise

    def _scan(self, scan: dict[str, Any]) -> dict[str, Any]:
        previous = self._snapshot
        session_was_valid = self._store.get_meta("session_valid")
        events: list[EventDraft] = []
        snapshot: Snapshot | None = None
        session_valid: bool | None = None
        try:
            session = self._upstream.check_session()
            if not session.valid:
                raise SessionExpiredError("EDUPLUS 登录态已失效", upstream_code=session.upstream_code)
            result = self._upstream.fetch(self._teach_class_cache)
        except SessionExpiredError as exc:
            session_valid = False
            if session_was_valid is not False:
                events.append(EventDraft(type="session.expired", task=None))
            scan.update(status="failed", error={"error": str(exc), "code": "upstream_session_expired"})
        except UpstreamError as exc:
            scan.update(status="failed", error={"error": str(exc), "code": "upstream_unavailable"})
        else:
            session_valid = True
            if session_was_valid is False:
                events.append(EventDraft(type="session.restored", task=None))
            tasks = merge_tasks(previous, result, seen_at=scan["started_at"])
            events.extend(diff_tasks(previous.tasks if previous else None, tasks))
            failures = result.failures
            snapshot = Snapshot(scanned_at="", courses=result.courses, tasks=tasks, failures=failures)
            scan.update(
                status="partially_failed" if failures else "succeeded",
                task_count=len(tasks),
                failures=[asdict(failure) for failure in failures],
            )

        scan.update(finished_at=self._iso_now(), event_count=len(events))
        if snapshot is not None:
            snapshot.scanned_at = scan["finished_at"]
        with self._lock:
            self._store.commit_scan(scan, events, snapshot=snapshot, session_valid=session_valid)
            if snapshot is not None:
                self._snapshot = snapshot
            self._running = None
        return dict(scan)

    def _iso_now(self) -> str:
        return self._clock().isoformat(timespec="seconds")
