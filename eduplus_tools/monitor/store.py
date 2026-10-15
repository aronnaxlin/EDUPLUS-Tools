from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..features.status import Course, ScanFailure, Task, parse_iso, task_to_api
from .diff import EventDraft


MAX_EVENTS = 5000


class CursorExpiredError(Exception):
    pass


@dataclass
class Snapshot:
    scanned_at: str
    courses: list[Course]
    tasks: dict[str, Task]
    failures: list[ScanFailure]


class StatusStore:
    """SQLite-backed snapshot, latest scan and event log for the status API."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, occurred_at TEXT NOT NULL, "
                "scan_id TEXT NOT NULL, task TEXT, changes TEXT NOT NULL)"
            )

    def get_meta(self, key: str) -> Any:
        with self._lock:
            return self._get_meta(key)

    def load_snapshot(self) -> Snapshot | None:
        data = self.get_meta("snapshot")
        if not isinstance(data, dict):
            return None
        return Snapshot(
            scanned_at=data["scanned_at"],
            courses=[Course(**course) for course in data["courses"]],
            tasks={task["id"]: Task(**task) for task in data["tasks"]},
            failures=[ScanFailure(**failure) for failure in data["failures"]],
        )

    def load_latest_scan(self) -> dict[str, Any] | None:
        scan = self.get_meta("latest_scan")
        return scan if isinstance(scan, dict) else None

    def commit_scan(
        self,
        scan: dict[str, Any],
        events: list[EventDraft],
        *,
        snapshot: Snapshot | None = None,
        session_valid: bool | None = None,
    ) -> None:
        """Atomically record a finished scan, its events and (if any) the new snapshot."""
        occurred_at = scan["finished_at"]
        now = parse_iso(occurred_at)
        with self._lock, self._conn:
            for event in events:
                self._conn.execute(
                    "INSERT INTO events (type, occurred_at, scan_id, task, changes) VALUES (?, ?, ?, ?, ?)",
                    (
                        event.type,
                        occurred_at,
                        scan["id"],
                        json.dumps(task_to_api(event.task, now), ensure_ascii=False) if event.task else None,
                        json.dumps(event.changes, ensure_ascii=False),
                    ),
                )
            if snapshot is not None:
                self._set_meta(
                    "snapshot",
                    {
                        "scanned_at": snapshot.scanned_at,
                        "courses": [asdict(course) for course in snapshot.courses],
                        "tasks": [asdict(task) for task in snapshot.tasks.values()],
                        "failures": [asdict(failure) for failure in snapshot.failures],
                    },
                )
            if session_valid is not None:
                self._set_meta("session_valid", session_valid)
            self._set_meta("latest_scan", scan)
            self._prune()

    def list_events(self, after: int | None, limit: int, types: list[str] | None = None) -> tuple[list[dict[str, Any]], str | None]:
        with self._lock:
            pruned_through = int(self._get_meta("pruned_through") or 0)
            if after is not None and after < pruned_through:
                raise CursorExpiredError
            sql = "SELECT seq, type, occurred_at, scan_id, task, changes FROM events WHERE seq > ?"
            params: list[Any] = [after or 0]
            if types:
                sql += f" AND type IN ({', '.join('?' for _ in types)})"
                params.extend(types)
            sql += " ORDER BY seq LIMIT ?"
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()

        items = [
            {
                "id": str(seq),
                "type": event_type,
                "occurred_at": occurred_at,
                "scan_id": scan_id,
                "task": json.loads(task) if task else None,
                "changes": json.loads(changes),
            }
            for seq, event_type, occurred_at, scan_id, task, changes in rows
        ]
        if items:
            return items, items[-1]["id"]
        return items, str(after) if after is not None else None

    def _get_meta(self, key: str) -> Any:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _set_meta(self, key: str, value: Any) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def _prune(self) -> None:
        row = self._conn.execute("SELECT MAX(seq) FROM events").fetchone()
        cutoff = (row[0] or 0) - MAX_EVENTS
        if cutoff > 0:
            self._conn.execute("DELETE FROM events WHERE seq <= ?", (cutoff,))
            self._set_meta("pruned_through", cutoff)
