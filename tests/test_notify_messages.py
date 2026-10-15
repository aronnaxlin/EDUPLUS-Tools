from __future__ import annotations

from eduplus_tools.notify.messages import format_due, format_event, format_remaining, format_todo, truncate

from notify_fixtures import api_task
from status_fixtures import FakeClock


NOW = FakeClock()()  # 2026-10-15 12:00, a Thursday


def test_format_due_relative_days() -> None:
    assert format_due("2026-10-15T23:59:59+08:00", NOW) == "今天 23:59"
    assert format_due("2026-10-16T08:00:00+08:00", NOW) == "明天 08:00"
    assert format_due("2026-10-22T23:59:59+08:00", NOW) == "10月22日 周四 23:59"
    assert format_due(None, NOW) == "无截止时间"


def test_format_remaining() -> None:
    assert format_remaining("2026-10-15T23:59:59+08:00", NOW) == "还剩 11 小时 59 分钟"
    assert format_remaining("2026-10-22T12:00:00+08:00", NOW) == "还剩 7 天"
    assert format_remaining("2026-10-15T12:30:00+08:00", NOW) == "还剩 30 分钟"
    assert format_remaining("2026-10-12T14:30:59+08:00", NOW) == "已逾期 2 天 21 小时"


def test_published_event_escapes_html() -> None:
    text = format_event({"type": "task.published", "task": api_task(title="<script> & 作业"), "changes": {}}, NOW)
    assert text is not None
    assert text.startswith("【新作业】操作系统-24级")
    assert "&lt;script&gt; &amp; 作业" in text
    assert "今天 23:59（还剩 11 小时 59 分钟）" in text


def test_submitted_event_is_not_sent() -> None:
    assert format_event({"type": "task.submitted", "task": api_task(state="submitted"), "changes": {}}, NOW) is None


def test_due_changed_and_graded_events() -> None:
    changed = format_event(
        {"type": "task.due_changed", "task": api_task(), "changes": {"due_at": {"from": "2026-10-14T23:59:59+08:00", "to": "2026-10-15T23:59:59+08:00"}}},
        NOW,
    )
    assert changed is not None and "原截止：10月14日 周三 23:59" in changed

    graded = format_event({"type": "task.graded", "task": {**api_task(state="graded"), "score": 90.0}, "changes": {}}, NOW)
    assert graded is not None and "得分：90 / 100" in graded


def test_session_events() -> None:
    assert "SESSION 已失效" in (format_event({"type": "session.expired", "task": None}, NOW) or "")
    assert format_event({"type": "session.restored", "task": None}, NOW) == "【登录态恢复】EDUPLUS 作业检测已恢复。"


def test_format_todo_groups_overdue() -> None:
    text = format_todo([api_task("homework:a"), api_task("homework:b", due_at="2026-10-12T14:30:59+08:00", overdue=True)], NOW)
    assert "<b>未提交（1）</b>" in text
    assert "<b>已逾期（1）</b>" in text
    assert format_todo([], NOW) == "没有未提交的作业或实验。"


def test_truncate_cuts_on_line_boundary() -> None:
    text = truncate("\n".join(f"第 {index} 行" for index in range(2000)))
    assert len(text) <= 4096
    assert text.endswith("……（已截断）")
