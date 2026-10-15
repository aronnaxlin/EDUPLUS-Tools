from __future__ import annotations

import argparse
import os
from pathlib import Path

from .bot import BotConfig, NotifierBot
from .clients import StatusApiClient, TelegramClient


def parse_hours(raw: str) -> tuple[float, ...]:
    try:
        hours = tuple(sorted({float(part) for part in raw.split(",") if part.strip()}, reverse=True))
    except ValueError:
        raise argparse.ArgumentTypeError("提醒时间必须是逗号分隔的小时数，例如 24,3") from None
    if any(value <= 0 for value in hours):
        raise argparse.ArgumentTypeError("提醒时间必须大于 0")
    return hours


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EDUPLUS 作业提醒 Telegram bot，读取状态检测 API")
    parser.add_argument("--status-url", default=os.getenv("EDUPLUS_STATUS_URL", "http://127.0.0.1:8000"), help="状态检测 API 的地址")
    parser.add_argument("--status-token", default=os.getenv("EDUPLUS_STATUS_TOKEN", ""), help="状态检测 API 的访问 token")
    parser.add_argument("--telegram-token", default=os.getenv("TELEGRAM_BOT_TOKEN", ""), help="BotFather 给的 bot token")
    parser.add_argument("--chat-id", default=os.getenv("TELEGRAM_CHAT_ID", ""), help="接收通知、允许发命令的 chat id")
    parser.add_argument("--reminder-hours", type=parse_hours, default=parse_hours(os.getenv("TELEGRAM_REMINDER_HOURS", "24,3")), help="截止前多少小时提醒，逗号分隔")
    parser.add_argument("--interval-seconds", type=int, default=int(os.getenv("TELEGRAM_POLL_INTERVAL_SECONDS", "60")), help="检查新事件和提醒的间隔")
    parser.add_argument("--state-file", default=os.getenv("TELEGRAM_STATE_FILE", "downloads/status/telegram-state.json"), help="保存推送进度的文件")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    missing = [name for name, value in (("--status-token", args.status_token), ("--telegram-token", args.telegram_token), ("--chat-id", args.chat_id)) if not value]
    if missing:
        parser.error(f"缺少 {', '.join(missing)}（也可以用对应的环境变量设置）")

    bot = NotifierBot(
        StatusApiClient(args.status_url, args.status_token),
        TelegramClient(args.telegram_token),
        BotConfig(
            chat_id=str(args.chat_id),
            state_file=Path(args.state_file),
            reminder_hours=args.reminder_hours,
            interval_seconds=max(10, args.interval_seconds),
        ),
    )
    print(f"EDUPLUS Telegram bot 已启动，读取 {args.status_url}，提醒时间：{', '.join(f'{hours:g} 小时' for hours in args.reminder_hours)}")
    try:
        bot.run_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
