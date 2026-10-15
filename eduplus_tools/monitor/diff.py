from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..features.status import Task


@dataclass
class EventDraft:
    type: str
    task: Task | None
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)


def change(before: Any, after: Any) -> dict[str, Any]:
    return {"from": before, "to": after}


def state_event_type(before: str, after: str) -> str | None:
    if after == "graded" and before != "graded":
        return "task.graded"
    if after == "submitted" and before == "not_submitted":
        return "task.submitted"
    if after == "not_submitted" and before in {"submitted", "graded"}:
        return "task.withdrawn"
    return None


def diff_tasks(previous: dict[str, Task] | None, current: dict[str, Task]) -> list[EventDraft]:
    """Compare two snapshots. `previous=None` means a baseline scan, which emits nothing."""
    if previous is None:
        return []

    events: list[EventDraft] = []
    for task_id, task in current.items():
        old = previous.get(task_id)
        if old is None:
            events.append(EventDraft(type="task.published", task=task))
            continue
        event_type = state_event_type(old.state, task.state)
        if event_type:
            changes = {"state": change(old.state, task.state)}
            if old.score != task.score:
                changes["score"] = change(old.score, task.score)
            events.append(EventDraft(type=event_type, task=task, changes=changes))
        if old.due_at != task.due_at:
            events.append(EventDraft(type="task.due_changed", task=task, changes={"due_at": change(old.due_at, task.due_at)}))

    for task_id, old in previous.items():
        if task_id not in current:
            events.append(EventDraft(type="task.removed", task=old))
    return events
