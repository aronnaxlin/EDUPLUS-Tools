from __future__ import annotations

import json
import sys
from datetime import datetime
from importlib import import_module
from unittest.mock import Mock

import pytest

from eduplus_tools.features.status import ScanFailure, SessionExpiredError, UpstreamError

from status_fixtures import FakeClock, FakeUpstream, course, task


cli = import_module("eduplus_tools.cli.main")
DEFAULT_IDS = [
    "homework:submitted", "homework:late", "homework:now", "homework:soon",
    "experiment:edge", "homework:graded", "experiment:no-due",
]


@pytest.fixture()
def status_cli(monkeypatch, tmp_path):
    upstream = FakeUpstream(
        courses=[course(), course("old", end="2026-01-31T23:59:59")],
        tasks=[
            task("experiment:no-due", due_at=None),
            task("homework:graded", state="graded", due_at="2026-10-15T13:30:00+08:00"),
            task("experiment:edge", due_at="2026-10-15T13:00:00+08:00"),
            task("homework:soon", due_at="2026-10-15T12:30:00+08:00"),
            task("homework:old", course_id="old", due_at="2026-10-15T12:30:00+08:00"),
            task("homework:now", due_at="2026-10-15T12:00:00+08:00"),
            task("homework:late", due_at="2026-10-15T11:00:00+08:00"),
            task("homework:submitted", state="submitted", due_at="2026-10-15T10:00:00+08:00"),
        ],
    )
    clock = Mock(wraps=datetime)
    clock.now.return_value = FakeClock()()
    monkeypatch.setattr(cli, "datetime", clock)
    monkeypatch.setattr(cli, "fetch_courses", Mock(side_effect=lambda client: list(upstream.courses)))
    monkeypatch.setattr(cli, "fetch_status", Mock(side_effect=lambda client: upstream.fetch({})))

    def invoke(*args):
        # An absolute missing path keeps tests away from the real local config.
        monkeypatch.setattr(sys, "argv", [
            "eduplus_tools", *args, "--config", str(tmp_path / "missing.json"), "--session", "test-session",
        ])
        return cli.main()

    return invoke, upstream


@pytest.mark.parametrize("json_output", [False, True])
def test_courses_needs_no_course_id_and_can_include_ended_courses(status_cli, capsys, json_output) -> None:
    invoke, _ = status_cli
    options = ["--json"] if json_output else []
    assert invoke("courses", *options) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    if json_output:
        items = json.loads(captured.out)["items"]
        assert [(item["id"], item["active"]) for item in items] == [("c1", True)]
        assert items[0]["name"] == "课程c1"
    else:
        assert captured.out == "c1  课程c1  2026-2027第一学期\n"

    assert invoke("courses", "--all-courses", *options) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    if json_output:
        assert [(item["id"], item["active"]) for item in json.loads(captured.out)["items"]] == [("c1", True), ("old", False)]
    else:
        assert "old  课程old  2026-2027第一学期  (已结课)" in captured.out


@pytest.mark.parametrize(("options", "expected"), [
    ([], DEFAULT_IDS),
    (["--state", "submitted", "--state", "graded"], ["homework:submitted", "homework:graded"]),
    (["--type", "experiment"], ["experiment:edge", "experiment:no-due"]),
    (["--type", "homework", "--type", "experiment"], DEFAULT_IDS),
    (["--overdue"], ["homework:late"]),
    (["--due-within", "1"], ["homework:soon"]),
    (["--due-within", "1.5"], ["homework:soon", "experiment:edge"]),
    (["--course-id", "old"], []),
    (["--all-courses", "--course-id", "old"], ["homework:old"]),
    (["--all-courses"], DEFAULT_IDS[:3] + ["homework:old"] + DEFAULT_IDS[3:]),
    (["--state", "not_submitted", "--type", "homework", "--course-id", "c1", "--due-within", "1"], ["homework:soon"]),
])
def test_tasks_json_filters_and_order(status_cli, capsys, options, expected) -> None:
    invoke, _ = status_cli
    assert invoke("tasks", "--json", *options) == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert captured.err == ""
    assert body["fetched_at"] == "2026-10-15T12:00:00+08:00"
    assert body["partial_failures"] == []
    assert [item["id"] for item in body["items"]] == expected
    assert "test-session" not in captured.out


def test_tasks_text_and_empty_result(status_cli, capsys) -> None:
    invoke, _ = status_cli
    assert invoke("tasks") == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "[已逾期] 10-15 周四 11:00 · 作业 · 课程c1 · 任务 homework:late" in captured.out
    assert "[已提交] 10-15 周四 10:00" in captured.out
    assert "[已批改] 10-15 周四 13:30" in captured.out
    assert "[未提交] 无截止时间 · 实验" in captured.out
    assert "test-session" not in captured.out

    assert invoke("tasks", "--course-id", "missing") == 0
    assert capsys.readouterr().out == "没有符合条件的作业或实验。\n"


def test_partial_failures_and_verbose_logs_do_not_pollute_json(status_cli, monkeypatch, capsys) -> None:
    invoke, upstream = status_cli
    upstream.failures = [ScanFailure(course_id="c1", type="experiment", error="网络不可用")]

    def fetch(client):
        assert client.verbose is True
        client.log("GET /fake-status")
        return upstream.fetch({})

    monkeypatch.setattr(cli, "fetch_status", fetch)
    assert invoke("tasks", "--json", "--verbose") == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["partial_failures"] == [{"course_id": "c1", "type": "experiment", "error": "网络不可用"}]
    assert body["items"][0]["course"] == {"id": "c1", "name": "课程c1"}
    assert body["items"][0]["overdue"] is False
    assert "GET /fake-status" in captured.err
    assert "警告：课程 c1 的实验没抓到：网络不可用" in captured.err
    assert "test-session" not in captured.out + captured.err


@pytest.mark.parametrize("command", ["courses", "tasks"])
@pytest.mark.parametrize(("error", "exit_code", "message"), [
    (SessionExpiredError("expired"), 2, "登录态已失效"),
    (UpstreamError("网络不可用"), 1, "网络不可用"),
])
def test_upstream_error_exit_codes(status_cli, monkeypatch, capsys, command, error, exit_code, message) -> None:
    invoke, _ = status_cli
    target = "fetch_courses" if command == "courses" else "fetch_status"
    monkeypatch.setattr(cli, target, Mock(side_effect=error))
    assert invoke(command, "--json") == exit_code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert "test-session" not in captured.err


@pytest.mark.parametrize("hours", ["-1", "0", "nan", "inf", "1e100", "invalid"])
def test_due_within_rejects_invalid_hours_before_fetching(status_cli, capsys, hours) -> None:
    invoke, _ = status_cli
    with pytest.raises(SystemExit) as exc:
        invoke("tasks", "--due-within", hours)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--due-within" in captured.err
    cli.fetch_status.assert_not_called()
