from __future__ import annotations

from datetime import timedelta

from eduplus_tools.notify.bot import BotConfig, BotState, NotifierBot

from notify_fixtures import FakeStatus, FakeTelegram, api_task
from status_fixtures import FakeClock


CHAT = "1001"


def make_bot(tmp_path, status: FakeStatus, telegram: FakeTelegram, clock: FakeClock | None = None) -> NotifierBot:
    config = BotConfig(chat_id=CHAT, state_file=tmp_path / "state.json", reminder_hours=(24, 3))
    return NotifierBot(status, telegram, config, clock=clock or FakeClock(), log=lambda message: None)


def test_first_run_skips_existing_events_then_forwards_new_ones(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    status.add_event("task.published", api_task("homework:old"))
    bot = make_bot(tmp_path, status, telegram)

    bot.forward_events()
    assert telegram.sent == []

    status.add_event("task.published", api_task("homework:new", title="新的作业"))
    status.add_event("task.submitted", api_task("homework:new", state="submitted"))
    bot.forward_events()
    assert len(telegram.sent) == 1
    assert "新的作业" in telegram.sent[0][1]
    assert telegram.sent[0][0] == CHAT
    assert BotState.load(tmp_path / "state.json").event_cursor == "3"


def test_first_run_on_empty_stream_still_forwards_later_events(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    bot = make_bot(tmp_path, status, telegram)
    bot.forward_events()
    status.add_event("task.published", api_task())
    bot.forward_events()
    assert len(telegram.sent) == 1


def test_failed_send_is_retried_without_skipping(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    bot = make_bot(tmp_path, status, telegram)
    bot.forward_events()
    status.add_event("task.published", api_task("homework:a", title="甲"))
    status.add_event("task.published", api_task("homework:b", title="乙"))

    telegram.fail_sends = 1
    bot.tick()
    assert telegram.sent == []

    bot.tick()
    texts = telegram.texts()
    assert len(texts) == 2
    assert "甲" in texts[0] and "乙" in texts[1]


def test_state_survives_restart(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    make_bot(tmp_path, status, telegram).forward_events()
    status.add_event("task.published", api_task())
    make_bot(tmp_path, status, telegram).forward_events()
    make_bot(tmp_path, status, telegram).forward_events()
    assert len(telegram.sent) == 1


def test_expired_cursor_resyncs_and_warns(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    bot = make_bot(tmp_path, status, telegram)
    bot.forward_events()
    status.add_event("task.published", api_task())
    bot.forward_events()
    status.add_event("task.published", api_task("homework:lost"))
    status.expire_next = True
    bot.forward_events()
    assert "已过期" in telegram.texts()[-1]
    assert bot.state.event_cursor == "2"


def test_reminders_fire_once_per_threshold(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    clock = FakeClock("2026-10-15T00:00:00")
    status.task_items = [api_task(due_at="2026-10-15T23:59:59+08:00")]
    bot = make_bot(tmp_path, status, telegram, clock)

    bot.send_reminders()
    bot.send_reminders()
    assert len(telegram.sent) == 1  # within 24h

    clock.value += timedelta(hours=21, minutes=30)
    bot.send_reminders()
    bot.send_reminders()
    assert len(telegram.sent) == 2  # within 3h
    assert "快截止了" in telegram.texts()[-1]


def test_reminder_starts_at_smallest_crossed_threshold(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    status.task_items = [api_task(due_at="2026-10-15T13:00:00+08:00")]  # 1h left at startup
    bot = make_bot(tmp_path, status, telegram)
    bot.send_reminders()
    bot.send_reminders()
    assert len(telegram.sent) == 1


def test_due_change_resets_reminders(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    status.task_items = [api_task(due_at="2026-10-15T13:00:00+08:00")]
    bot = make_bot(tmp_path, status, telegram)
    bot.forward_events()
    bot.send_reminders()

    moved = api_task(due_at="2026-10-16T10:00:00+08:00")
    status.task_items = [moved]
    status.add_event("task.due_changed", moved, {"due_at": {"from": "2026-10-15T13:00:00+08:00", "to": moved["due_at"]}})
    bot.tick()
    texts = telegram.texts()
    assert "截止时间变更" in texts[-2]
    assert "快截止了" in texts[-1]


def test_submitted_or_overdue_tasks_get_no_reminder(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    status.task_items = [
        api_task("homework:done", state="submitted"),
        api_task("homework:late", due_at="2026-10-15T08:00:00+08:00", overdue=True),
    ]
    make_bot(tmp_path, status, telegram).send_reminders()
    assert telegram.sent == []


def message(text: str, chat_id: str = CHAT, update_id: int = 1) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": int(chat_id)}, "text": text}}


def test_commands(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    status.task_items = [api_task()]
    bot = make_bot(tmp_path, status, telegram)

    telegram.updates = [message("/todo", update_id=5), message("/status@eduplus_bot", update_id=6), message("/scan", update_id=7), message("你好", update_id=8)]
    bot.poll_commands(timeout=1)
    texts = telegram.texts()
    assert len(texts) == 3
    assert "未提交（1）" in texts[0]
    assert "登录态：有效" in texts[1] and "任务数：145" in texts[1]
    assert texts[2] == "已开始扫描，有变化会直接推送。"
    assert BotState.load(tmp_path / "state.json").update_offset == 9


def test_other_chats_are_refused(tmp_path) -> None:
    status, telegram = FakeStatus(), FakeTelegram()
    bot = make_bot(tmp_path, status, telegram)
    telegram.updates = [message("/todo", chat_id="2002")]
    bot.poll_commands(timeout=1)
    assert telegram.sent == [("2002", "这个 bot 只服务指定的聊天。你的 chat id 是 2002。")]
