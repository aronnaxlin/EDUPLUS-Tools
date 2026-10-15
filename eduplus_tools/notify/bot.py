from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ..core.times import CHINA_TZ, parse_iso
from .clients import CursorExpiredError, StatusApiError, TelegramError
from .messages import format_event, format_reminder, format_status, format_todo


HELP_TEXT = (
    "EDUPLUS 作业提醒\n"
    "/todo 未提交的作业和实验\n"
    "/status 登录态和最近一次扫描\n"
    "/scan 立即扫描一次"
)


class StatusApi(Protocol):
    def events(self, after: str | None, limit: int = 50) -> dict[str, Any]: ...

    def tasks(self, **params: Any) -> dict[str, Any]: ...

    def session(self) -> dict[str, Any]: ...

    def latest_scan(self) -> dict[str, Any] | None: ...

    def start_scan(self) -> dict[str, Any]: ...


class Telegram(Protocol):
    def send_message(self, chat_id: str, text: str) -> None: ...

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class BotConfig:
    chat_id: str
    state_file: Path
    reminder_hours: tuple[float, ...] = (24, 3)
    interval_seconds: int = 60


@dataclass
class BotState:
    synced: bool = False
    event_cursor: str | None = None
    update_offset: int | None = None
    # task id -> {"due_at": ..., "hours": smallest reminder threshold already sent}
    reminders: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> BotState:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            synced=bool(data.get("synced")),
            event_cursor=data.get("event_cursor"),
            update_offset=data.get("update_offset"),
            reminders=dict(data.get("reminders") or {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(self.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)


class NotifierBot:
    def __init__(
        self,
        status: StatusApi,
        telegram: Telegram,
        config: BotConfig,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(CHINA_TZ),
        log: Callable[[str], None] = print,
    ) -> None:
        self.status = status
        self.telegram = telegram
        self.config = config
        self.clock = clock
        self.log = log
        self.state = BotState.load(config.state_file)

    def run_forever(self) -> None:
        next_tick = 0.0
        while True:
            if time.monotonic() >= next_tick:
                self.tick()
                next_tick = time.monotonic() + self.config.interval_seconds
            try:
                self.poll_commands(timeout=max(1, min(30, int(next_tick - time.monotonic()))))
            except TelegramError as exc:
                self.log(f"读取 Telegram 消息失败：{exc}")
                time.sleep(5)

    def tick(self) -> None:
        for step in (self.forward_events, self.send_reminders):
            try:
                step()
            except (StatusApiError, TelegramError) as exc:
                self.log(f"{step.__name__} 失败，下一轮重试：{exc}")

    def forward_events(self) -> None:
        if not self.state.synced:
            self._skip_to_latest_event()
            self.state.synced = True
            self._save()
            return
        while True:
            try:
                page = self.status.events(after=self.state.event_cursor, limit=50)
            except CursorExpiredError:
                self._skip_to_latest_event()
                self._save()
                self._send("【提示】有一部分变化事件已过期，没能推送。用 /todo 查看当前状态。")
                return
            if not page["items"]:
                return
            now = self.clock()
            for event in page["items"]:
                text = format_event(event, now)
                if text:
                    self._send(text)  # on failure the cursor stays before this event
                if event["type"] == "task.due_changed" and event.get("task"):
                    self.state.reminders.pop(event["task"]["id"], None)
                self.state.event_cursor = event["id"]
                self._save()

    def send_reminders(self) -> None:
        if not self.config.reminder_hours:
            return
        now = self.clock()
        horizon = now + timedelta(hours=max(self.config.reminder_hours))
        body = self.status.tasks(
            state="not_submitted",
            due_after=now.isoformat(timespec="seconds"),
            due_before=horizon.isoformat(timespec="seconds"),
        )
        for task in body["items"]:
            due = parse_iso(task["due_at"])
            if due is None:
                continue
            hours_left = (due - now).total_seconds() / 3600
            crossed = [hours for hours in self.config.reminder_hours if hours_left <= hours]
            if not crossed:
                continue
            level = min(crossed)
            sent = self.state.reminders.get(task["id"])
            if sent and sent.get("due_at") == task["due_at"] and sent.get("hours", float("inf")) <= level:
                continue
            self._send(format_reminder(task, now))
            self.state.reminders[task["id"]] = {"due_at": task["due_at"], "hours": level}
            self._save()

        stale_before = now - timedelta(days=1)
        stale = [task_id for task_id, sent in self.state.reminders.items() if (parse_iso(sent.get("due_at")) or now) < stale_before]
        for task_id in stale:
            del self.state.reminders[task_id]
        if stale:
            self._save()

    def poll_commands(self, timeout: int) -> None:
        for update in self.telegram.get_updates(self.state.update_offset, timeout):
            self.state.update_offset = int(update["update_id"]) + 1
            message = update.get("message")
            if isinstance(message, dict):
                try:
                    self.handle_message(message)
                except (StatusApiError, TelegramError) as exc:
                    self.log(f"处理命令失败：{exc}")
            self._save()

    def handle_message(self, message: dict[str, Any]) -> None:
        chat_id = str((message.get("chat") or {}).get("id") or "")
        text = str(message.get("text") or "").strip()
        if not text.startswith("/") or not chat_id:
            return
        command = text.split()[0].split("@")[0].lower()
        if chat_id != self.config.chat_id:
            self.telegram.send_message(chat_id, f"这个 bot 只服务指定的聊天。你的 chat id 是 {chat_id}。")
            return

        if command in {"/start", "/help"}:
            reply = HELP_TEXT
        elif command == "/todo":
            body = self.status.tasks(state="not_submitted")
            reply = format_todo(body["items"], self.clock())
        elif command == "/status":
            reply = format_status(self.status.session(), self.status.latest_scan(), self.clock())
        elif command == "/scan":
            scan = self.status.start_scan()
            reply = "已开始扫描，有变化会直接推送。" if scan.get("status") == "running" else "扫描已完成。"
        else:
            reply = "不认识这个命令。\n" + HELP_TEXT
        self._send(reply)

    def _skip_to_latest_event(self) -> None:
        cursor = None
        while True:
            page = self.status.events(after=cursor, limit=200)
            if not page["items"]:
                break
            cursor = page["next_cursor"]
        self.state.event_cursor = cursor

    def _send(self, text: str) -> None:
        self.telegram.send_message(self.config.chat_id, text)

    def _save(self) -> None:
        self.state.save(self.config.state_file)
