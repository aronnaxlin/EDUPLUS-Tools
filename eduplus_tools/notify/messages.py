from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from ..core.times import parse_iso


WEEKDAYS = "一二三四五六日"
TYPE_LABELS = {"homework": "作业", "experiment": "实验"}
TELEGRAM_TEXT_LIMIT = 4096


def escape(value: Any) -> str:
    return html.escape(str(value), quote=False)


def format_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def format_due(due_at: str | None, now: datetime) -> str:
    due = parse_iso(due_at)
    if due is None:
        return "无截止时间"
    due = due.astimezone(now.tzinfo)
    days = (due.date() - now.date()).days
    if days == 0:
        day = "今天"
    elif days == 1:
        day = "明天"
    else:
        day = f"{due.month}月{due.day}日 周{WEEKDAYS[due.weekday()]}"
    return f"{day} {due:%H:%M}"


def format_remaining(due_at: str | None, now: datetime) -> str:
    due = parse_iso(due_at)
    if due is None:
        return ""
    seconds = int((due - now).total_seconds())
    prefix = "还剩" if seconds >= 0 else "已逾期"
    minutes = abs(seconds) // 60
    days, rest = divmod(minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{prefix} {days} 天 {hours} 小时" if hours else f"{prefix} {days} 天"
    if hours:
        return f"{prefix} {hours} 小时 {minutes} 分钟" if minutes else f"{prefix} {hours} 小时"
    return f"{prefix} {max(minutes, 1)} 分钟"


def task_lines(task: dict[str, Any], now: datetime) -> list[str]:
    due = format_due(task.get("due_at"), now)
    remaining = format_remaining(task.get("due_at"), now)
    return [
        f"<b>{escape(task['title'])}</b>",
        f"截止：{due}" + (f"（{remaining}）" if remaining else ""),
        f'<a href="{html.escape(task["url"])}">打开课程页</a>',
    ]


def headline(title: str, task: dict[str, Any]) -> str:
    return f"【{title}】{escape(task['course']['name'])}"


def format_event(event: dict[str, Any], now: datetime) -> str | None:
    """Telegram HTML text for an event, or None when the event is not worth a message."""
    event_type = event["type"]
    task = event.get("task")
    if event_type == "session.expired":
        return "【登录态失效】EDUPLUS 的 SESSION 已失效，作业检测已暂停。\n更新服务端 config.json 里的 SESSION 后会自动恢复。"
    if event_type == "session.restored":
        return "【登录态恢复】EDUPLUS 作业检测已恢复。"
    if not task:
        return None

    label = TYPE_LABELS.get(task["type"], "任务")
    changes = event.get("changes") or {}
    if event_type == "task.published":
        return "\n".join([headline(f"新{label}", task), *task_lines(task, now)])
    if event_type == "task.due_changed":
        old_due = (changes.get("due_at") or {}).get("from")
        return "\n".join([headline(f"{label}截止时间变更", task), *task_lines(task, now), f"原截止：{format_due(old_due, now)}"])
    if event_type == "task.graded":
        lines = [headline(f"{label}已批改", task), f"<b>{escape(task['title'])}</b>"]
        if task.get("score") is not None:
            total = f" / {format_number(task['total_score'])}" if task.get("total_score") is not None else ""
            lines.append(f"得分：{format_number(task['score'])}{total}")
        lines.append(f'<a href="{html.escape(task["url"])}">打开课程页</a>')
        return "\n".join(lines)
    if event_type == "task.withdrawn":
        return "\n".join([headline(f"{label}变回未提交", task), *task_lines(task, now), "可能是你撤回了，也可能是老师打回，需要重新提交。"])
    if event_type == "task.removed":
        return "\n".join([headline(f"{label}已被删除", task), f"<b>{escape(task['title'])}</b>"])
    return None


def format_reminder(task: dict[str, Any], now: datetime) -> str:
    label = TYPE_LABELS.get(task["type"], "任务")
    return "\n".join([headline(f"{label}快截止了", task), *task_lines(task, now)])


def format_todo(tasks: list[dict[str, Any]], now: datetime) -> str:
    upcoming = [task for task in tasks if not task["overdue"]]
    overdue = [task for task in tasks if task["overdue"]]
    if not tasks:
        return "没有未提交的作业或实验。"

    def line(task: dict[str, Any]) -> str:
        label = TYPE_LABELS.get(task["type"], "任务")
        due = format_due(task.get("due_at"), now)
        return f'• <a href="{html.escape(task["url"])}">{escape(task["title"])}</a>\n  {label} · {escape(task["course"]["name"])} · {due}'

    parts = []
    if upcoming:
        parts.append(f"<b>未提交（{len(upcoming)}）</b>\n" + "\n".join(line(task) for task in upcoming))
    if overdue:
        parts.append(f"<b>已逾期（{len(overdue)}）</b>\n" + "\n".join(line(task) for task in overdue))
    return truncate("\n\n".join(parts))


def format_status(session: dict[str, Any], scan: dict[str, Any] | None, now: datetime) -> str:
    lines = [f"登录态：{'有效' if session.get('valid') else '已失效'}"]
    if scan is None:
        lines.append("还没有扫描记录。")
    else:
        status_labels = {"running": "进行中", "succeeded": "成功", "partially_failed": "部分失败", "failed": "失败"}
        finished = scan.get("finished_at") or scan.get("started_at")
        when = format_due(finished, now) if finished else "-"
        lines.append(f"最近扫描：{when}，{status_labels.get(scan.get('status'), scan.get('status'))}")
        if scan.get("task_count") is not None:
            lines.append(f"任务数：{scan['task_count']}")
        if scan.get("error"):
            lines.append(f"错误：{escape(scan['error'].get('error'))}")
        for failure in scan.get("failures") or []:
            lines.append(f"失败：{escape(failure['course_id'])} {escape(failure['type'])}：{escape(failure['error'])}")
    return truncate("\n".join(lines))


def truncate(text: str) -> str:
    if len(text) <= TELEGRAM_TEXT_LIMIT:
        return text
    cut = text.rfind("\n", 0, TELEGRAM_TEXT_LIMIT - 20)
    return text[: cut if cut > 0 else TELEGRAM_TEXT_LIMIT - 20] + "\n……（已截断）"
