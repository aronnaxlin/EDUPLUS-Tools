from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from ..core.client import EduplusClient
from ..core.config import load_config
from ..core.times import CHINA_TZ
from ..features.homework import scrape_homework
from ..features.ppt import download_ppt_files
from ..features.status import (
    TASK_STATES,
    TASK_TYPES,
    SessionExpiredError,
    UpstreamError,
    course_is_active,
    course_to_api,
    fetch_courses,
    fetch_status,
    filter_tasks,
    task_is_overdue,
    task_to_api,
)


STATUS_COMMANDS = {"courses", "tasks"}
STATE_LABELS = {"not_submitted": "未提交", "submitted": "已提交", "graded": "已批改"}
TYPE_LABELS = {"homework": "作业", "experiment": "实验"}
WEEKDAYS = "一二三四五六日"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified EDUPLUS courseware and homework tool.")
    parser.add_argument(
        "command",
        choices=["all", "ppt", "homework", "courses", "tasks"],
        nargs="?",
        default="all",
        help="all/ppt/homework download one course (needs --course-id); courses lists your courses; tasks lists homework and experiment status across courses. Default: all",
    )
    parser.add_argument("--config", "--config-file", dest="config", default="config.json", help="JSON config file. Default: config.json")
    parser.add_argument("--config-json", help='Optional JSON config, e.g. {"session":"...","course_id":"..."}')
    parser.add_argument("--session", help="U+ SESSION/x-access-token")
    parser.add_argument("--course-id", "--course_id", dest="course_id", help="U+ course ID")
    parser.add_argument("--course-name", "--course_name", dest="course_name", help="Optional course name override")
    parser.add_argument("--hm-lvt", "--hm_lvt", dest="hm_lvt", help="Optional Hm_lvt cookie value")
    parser.add_argument("--base-url", default=None, help="U+ host")
    parser.add_argument("--output", "-o", default=None, help="Output root directory. Default: downloads")
    parser.add_argument("--timeout", type=int, default=None, help="HTTP timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="For ppt: list files and signed URLs without downloading")
    parser.add_argument("--overwrite", action="store_true", help="For ppt: overwrite existing files")
    parser.add_argument(
        "--homework-answer-mode",
        choices=["plain", "answers", "both"],
        default="plain",
        help="For homework Markdown: plain=no answers, answers=with answers, both=generate both. Default: plain",
    )
    parser.add_argument(
        "--homework-status-mode",
        choices=["all", "done", "undone"],
        default="all",
        help="For homework: all=done and undone, done=only completed, undone=only unfinished. Default: all",
    )
    parser.add_argument("--skip-existing-homework-convert", action="store_true", help="Only convert homework JSON fetched in this run")
    parser.add_argument("--verbose", action="store_true", help="Print extra request details")
    status_group = parser.add_argument_group("courses / tasks")
    status_group.add_argument("--json", action="store_true", help="Print machine-readable JSON (same task fields as the /api/v1 status API)")
    status_group.add_argument("--state", action="append", choices=TASK_STATES, help="Only tasks in this state; repeatable")
    status_group.add_argument("--type", action="append", choices=TASK_TYPES, help="Only homework or experiment; repeatable")
    status_group.add_argument("--due-within", type=float, metavar="HOURS", help="Only tasks due within the next HOURS (excludes overdue)")
    status_group.add_argument("--overdue", action="store_true", help="Only unsubmitted tasks past their deadline")
    status_group.add_argument("--all-courses", action="store_true", help="Include courses that have already ended")
    return parser


def format_due(due_at: str | None) -> str:
    if not due_at:
        return "无截止时间"
    due = datetime.fromisoformat(due_at)
    return f"{due:%m-%d} 周{WEEKDAYS[due.weekday()]} {due:%H:%M}"


def run_status_command(args: argparse.Namespace) -> int:
    config = load_config(
        config_file=args.config,
        config_json=args.config_json,
        session=args.session,
        base_url=args.base_url,
        timeout=args.timeout,
        require_course_id=False,
    )
    client = EduplusClient(config, verbose=args.verbose, log=lambda message: print(message, file=sys.stderr))
    now = datetime.now(CHINA_TZ)
    try:
        if args.command == "courses":
            courses = [course for course in fetch_courses(client) if args.all_courses or course_is_active(course, now)]
            if args.json:
                print(json.dumps({"items": [course_to_api(course, now) for course in courses]}, ensure_ascii=False, indent=2))
            else:
                for course in courses:
                    ended = "" if course_is_active(course, now) else "  (已结课)"
                    print(f"{course.id}  {course.name}  {course.term or ''}{ended}")
            return 0

        result = fetch_status(client)
    except SessionExpiredError:
        print("EDUPLUS 登录态已失效：请在浏览器重新登录，更新 config.json 里的 session，或用 --session 传入。", file=sys.stderr)
        return 2
    except UpstreamError as exc:
        print(f"请求 EDUPLUS 失败：{exc}", file=sys.stderr)
        return 1

    tasks = filter_tasks(
        result.tasks,
        result.courses,
        now,
        types=args.type or (),
        states=args.state or (),
        course_ids={args.course_id} if args.course_id else (),
        active_courses_only=not args.all_courses,
        overdue=True if args.overdue else None,
        due_after=now if args.due_within is not None else None,
        due_before=now + timedelta(hours=args.due_within) if args.due_within is not None else None,
    )
    if args.json:
        payload = {
            "fetched_at": now.isoformat(timespec="seconds"),
            "items": [task_to_api(task, now) for task in tasks],
            "partial_failures": [asdict(failure) for failure in result.failures],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not tasks:
            print("没有符合条件的作业或实验。")
        for task in tasks:
            state = "已逾期" if task_is_overdue(task, now) else STATE_LABELS[task.state]
            print(f"[{state}] {format_due(task.due_at)} · {TYPE_LABELS[task.type]} · {task.course_name} · {task.title}")
    for failure in result.failures:
        print(f"警告：课程 {failure.course_id} 的{TYPE_LABELS[failure.type]}没抓到：{failure.error}", file=sys.stderr)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.due_within is not None:
        try:
            if args.due_within <= 0:
                raise ValueError
            datetime.now(CHINA_TZ) + timedelta(hours=args.due_within)
        except (ValueError, OverflowError):
            parser.error("--due-within must be positive, finite hours within the supported datetime range")
    if args.command in STATUS_COMMANDS:
        return run_status_command(args)
    config = load_config(
        config_file=args.config,
        config_json=args.config_json,
        session=args.session,
        course_id=args.course_id,
        hm_lvt=args.hm_lvt,
        course_name=args.course_name,
        base_url=args.base_url,
        output=args.output,
        timeout=args.timeout,
    )
    client = EduplusClient(config, verbose=args.verbose)
    output_root = Path(config.output)

    print("=" * 60)
    print("EDUPLUS Tools")
    print("=" * 60)
    if config.config_path:
        print(f"Config: {config.config_path}")
    print(f"Course ID: {config.course_id}")
    print(f"Output: {output_root}")
    print("=" * 60)

    status = 0
    if args.command in {"all", "ppt"}:
        status = max(
            status,
            download_ppt_files(
                client,
                course_id=config.course_id,
                course_name=config.course_name,
                output_root=output_root,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
            ),
        )

    if args.command in {"all", "homework"} and not args.dry_run:
        status = max(
            status,
            scrape_homework(
                client,
                course_id=config.course_id,
                output_root=output_root,
                convert_existing=not args.skip_existing_homework_convert,
                answer_mode=args.homework_answer_mode,
                status_mode=args.homework_status_mode,
            ),
        )
    elif args.command == "all" and args.dry_run:
        print("Skipping homework because --dry-run only applies to ppt downloads.")

    return status


if __name__ == "__main__":
    raise SystemExit(main())
