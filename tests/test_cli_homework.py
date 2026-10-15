from __future__ import annotations

import json
import sys
from importlib import import_module
from unittest.mock import Mock

import pytest

from eduplus_tools.core.client import EduplusClient
from eduplus_tools.features import homework

from status_fixtures import homework_item


cli = import_module("eduplus_tools.cli.main")


@pytest.mark.parametrize(("answer_mode", "status_mode", "sub_status"), [
    ("plain", "undone", 0),
    ("answers", "done", 1),
    ("both", "all", 0),
])
def test_homework_exports_without_logging_credentials(monkeypatch, tmp_path, capsys, answer_mode, status_mode, sub_status) -> None:
    def api_json(client, path, referer=None):
        if path == "/api/course/homeworks/published/student?courseId=c1":
            return {"success": True, "data": [homework_item(sub_status=sub_status)]}
        if path == "/api/course/homeworkQuestions/student?homeworkId=hw1":
            return {"code": 2000000, "data": [{"id": "q1", "orderNumber": 1}]}
        assert path == "/api/course/homeworkQuestions/q1/student/detail"
        return {"code": 2000000, "data": {
            "qsnType": 1, "titleText": "<p>进程是什么？</p>",
            "options": [{"id": "A", "optionContent": "运行中的程序"}],
            "answer": "A", "userAnswer": "A",
        }}

    monkeypatch.setattr(EduplusClient, "api_json", api_json)
    monkeypatch.setattr(homework.time, "sleep", Mock())
    output = tmp_path / "export"
    monkeypatch.setattr(sys, "argv", [
        "eduplus_tools", "homework", "--course-id", "c1", "--output", str(output),
        "--config", str(tmp_path / "missing.json"), "--session", "test-secret",
        "--hm-lvt", "test-cookie", "--homework-answer-mode", answer_mode,
        "--homework-status-mode", status_mode, "--skip-existing-homework-convert",
    ])
    assert cli.main() == 0
    captured = capsys.readouterr()
    assert "test-secret" not in captured.out + captured.err
    assert "test-cookie" not in captured.out + captured.err
    assert "SESSION:" not in captured.out
    assert "Hm_lvt:" not in captured.out

    json_files = list((output / "homework" / "json").rglob("*.json"))
    md_files = list((output / "homework" / "markdown").rglob("*.md"))
    assert len(json_files) == 1
    assert len(md_files) == (2 if answer_mode == "both" else 1)
    assert json_files[0].parent.name == ("做过" if sub_status else "没做过")
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert data["question_count"] == 1
    detail = data["questions"][0]["detail"]
    assert ("answer" in detail) == (answer_mode != "plain")
    assert ("userAnswer" in detail) == (answer_mode != "plain")
    for path in md_files:
        text = path.read_text(encoding="utf-8")
        assert "进程是什么？" in text
        assert ("正确答案" in text) == ("带答案" in path.stem)
