from __future__ import annotations

from typing import Any

from eduplus_tools.features.status import parse_iso
from eduplus_tools.notify.clients import CursorExpiredError, TelegramError


def api_task(task_id: str = "homework:a", *, due_at: str | None = "2026-10-15T23:59:59+08:00", state: str = "not_submitted", overdue: bool = False, title: str = "第一章 引论 作业") -> dict[str, Any]:
    return {
        "id": task_id,
        "type": task_id.split(":", 1)[0],
        "course": {"id": "c1", "name": "操作系统-24级"},
        "title": title,
        "state": state,
        "due_at": due_at,
        "published_at": None,
        "first_seen_at": "2026-10-15T10:00:00+08:00",
        "overdue": overdue,
        "late_submission_allowed": True,
        "score": None,
        "total_score": 100.0,
        "deliverables": [],
        "url": "https://www.eduplus.net/course/course/c1/HomeworkPage?userRole=",
        "upstream": {},
    }


class FakeStatus:
    def __init__(self) -> None:
        self.event_log: list[dict[str, Any]] = []
        self.task_items: list[dict[str, Any]] = []
        self.expire_next = False
        self.session_valid = True

    def add_event(self, event_type: str, task: dict[str, Any] | None = None, changes: dict | None = None) -> None:
        self.event_log.append(
            {"id": str(len(self.event_log) + 1), "type": event_type, "occurred_at": "2026-10-15T12:00:00+08:00", "scan_id": "s", "task": task, "changes": changes or {}}
        )

    def events(self, after: str | None, limit: int = 50) -> dict[str, Any]:
        if self.expire_next and after is not None:
            self.expire_next = False
            raise CursorExpiredError("gone", 410, "cursor_expired")
        start = int(after or 0)
        items = [event for event in self.event_log if int(event["id"]) > start][:limit]
        return {"items": items, "next_cursor": items[-1]["id"] if items else after}

    def tasks(self, **params: Any) -> dict[str, Any]:
        due_after = parse_iso(params.get("due_after"))
        due_before = parse_iso(params.get("due_before"))
        items = []
        for task in self.task_items:
            due = parse_iso(task["due_at"])
            if params.get("state") and task["state"] != params["state"]:
                continue
            if (due_after or due_before) and due is None:
                continue
            if due_after and due <= due_after:
                continue
            if due_before and due >= due_before:
                continue
            items.append(task)
        return {"items": items, "scanned_at": "2026-10-15T12:00:00+08:00", "partial_failures": []}

    def session(self) -> dict[str, Any]:
        return {"valid": self.session_valid, "checked_at": "2026-10-15T12:00:00+08:00", "upstream_code": 2000000}

    def latest_scan(self) -> dict[str, Any] | None:
        return {"id": "s", "status": "succeeded", "trigger": "schedule", "started_at": "2026-10-15T11:59:00+08:00", "finished_at": "2026-10-15T12:00:00+08:00", "task_count": 145, "event_count": 0, "failures": []}

    def start_scan(self) -> dict[str, Any]:
        return {"id": "s2", "status": "running", "trigger": "manual", "started_at": "2026-10-15T12:00:00+08:00"}


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.updates: list[dict[str, Any]] = []
        self.fail_sends = 0

    def send_message(self, chat_id: str, text: str) -> None:
        if self.fail_sends:
            self.fail_sends -= 1
            raise TelegramError("network down")
        self.sent.append((chat_id, text))

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        updates = [update for update in self.updates if offset is None or update["update_id"] >= offset]
        self.updates = []
        return updates

    def texts(self) -> list[str]:
        return [text for _, text in self.sent]
