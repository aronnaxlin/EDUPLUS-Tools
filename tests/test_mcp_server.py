from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from eduplus_tools.core.client import EduplusClient
from eduplus_tools.core.config import clear_runtime_overrides, load_config
from eduplus_tools.features import homework, ppt
from eduplus_tools.mcp.server import create_mcp_server
from eduplus_tools.monitor.service import StatusMonitor
from eduplus_tools.monitor.store import StatusStore
from eduplus_tools.web import server
from eduplus_tools.web.status_api import StatusApi

from status_fixtures import FakeClock, FakeUpstream, course, homework_item, task


@pytest.fixture(autouse=True)
def _clean_runtime_state(monkeypatch, tmp_path):
    clear_runtime_overrides()
    monkeypatch.setenv("EDUPLUS_DATA_DIR", str(tmp_path / "data"))
    for key in (
        "EDUPLUS_SESSION",
        "EDUPLUS_COURSE_ID",
        "EDUPLUS_HM_LVT",
        "EDUPLUS_COURSE_NAME",
        "EDUPLUS_BASE_URL",
        "EDUPLUS_OUTPUT",
        "EDUPLUS_TIMEOUT",
        "EDUPLUS_CONFIG",
        "EDUPLUS_MCP_TOKEN",
        "EDUPLUS_MCP_ALLOWED_HOSTS",
        "EDUPLUS_MCP_ALLOW_NO_AUTH",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    clear_runtime_overrides()


def _mcp_post(client: TestClient, payload: dict, *, path: str = "/mcp", headers: dict[str, str] | None = None):
    merged_headers = {
        "Host": "localhost:8000",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **(headers or {}),
    }
    return client.post(path, json=payload, headers=merged_headers)


def _parse_mcp_response(response) -> dict:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line.removeprefix("data: "))
        raise AssertionError(f"Empty SSE payload: {response.text}")
    return response.json()


def test_load_config_reads_env_and_runtime_overrides(tmp_path) -> None:
    missing_cfg = tmp_path / "missing.json"
    with pytest.raises(SystemExit):
        load_config(config_file=missing_cfg, require_course_id=False)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("EDUPLUS_SESSION", "env-token")
    monkeypatch.setenv("EDUPLUS_COURSE_ID", "env-course")
    try:
        cfg = load_config(config_file=missing_cfg)
        assert cfg.session == "env-token"
        assert cfg.course_id == "env-course"
    finally:
        monkeypatch.undo()


def test_asgi_app_serves_mcp_over_http_with_allowed_tunnel_host_and_token(monkeypatch, tmp_path) -> None:
    upstream = FakeUpstream(courses=[course("c1")], tasks=[task("homework:t1")])
    monitor = StatusMonitor(StatusStore(":memory:"), upstream, clock=FakeClock())
    monitor.scan_now()

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"session": "secret-token", "course_id": "c1"}), encoding="utf-8")

    cfg = server.WebServerConfig(
        host="0.0.0.0",
        port=8000,
        enable_local_output=True,
        auto_delete_public_downloads=False,
        public_job_ttl_seconds=1800,
        cleanup_interval_seconds=60,
        public_output_root=tmp_path / "web-jobs",
        bundle_root=tmp_path / "web-bundles",
        local_output_root=tmp_path / "downloads",
        status_token="status-secret",
        status_db=tmp_path / "status.sqlite3",
        status_scan_interval_seconds=600,
        status_config=str(config_path),
        mcp_enabled=True,
        mcp_path="/mcp",
        mcp_token="",
        mcp_allowed_hosts="eduplus-tunnel.example.com",
        mcp_allow_no_auth=True,
    )
    monkeypatch.setattr(server, "SERVER_CONFIG", cfg)
    monkeypatch.setattr(server, "STATUS_MONITOR", monitor)
    monkeypatch.setattr(server, "STATUS_API", StatusApi(monitor, "status-secret"))

    app = server.create_asgi_app(cfg, start_background_tasks=False)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["mcp_enabled"] is True
        assert health.json()["mcp_path"] == "/mcp"

        # Unlisted Host headers are rejected (DNS rebinding protection stays on).
        evil = _mcp_post(
            client,
            {"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}},
            headers={"Host": "attacker.example"},
        )
        assert evil.status_code == 421

        # The configured tunnel host is accepted.
        init_resp = _mcp_post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
            headers={"Host": "eduplus-tunnel.example.com"},
        )
        assert init_resp.status_code == 200
        init_body = _parse_mcp_response(init_resp)
        assert init_body["result"]["serverInfo"]["name"] == "EDUPLUS-Tools"

        tools_resp = _mcp_post(
            client,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={"Host": "eduplus-tunnel.example.com"},
        )
        assert tools_resp.status_code == 200
        tool_names = {tool["name"] for tool in _parse_mcp_response(tools_resp)["result"]["tools"]}
        assert {
            "check_session",
            "configure_session",
            "list_courses",
            "list_tasks",
            "list_courseware",
            "download_courseware",
            "fetch_homework",
            "list_downloads",
            "read_download",
            "get_status_monitor",
            "trigger_status_scan",
        }.issubset(tool_names)

        monitor_resp = _mcp_post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_status_monitor", "arguments": {"include_tasks": True}},
            },
            headers={"Host": "eduplus-tunnel.example.com"},
        )
        assert monitor_resp.status_code == 200
        monitor_body = _parse_mcp_response(monitor_resp)
        structured = monitor_body["result"]["structuredContent"]
        assert structured["enabled"] is True
        assert structured["task_count"] == 1
        assert structured["tasks"][0]["id"] == "homework:t1"

    # When EDUPLUS_MCP_TOKEN is configured, /mcp enforces Bearer auth.
    secured_cfg = server.WebServerConfig(**{**cfg.__dict__, "mcp_token": "mcp-secret", "mcp_allow_no_auth": False})
    secured_app = server.create_asgi_app(secured_cfg, start_background_tasks=False)
    with TestClient(secured_app) as secured_client:
        denied = _mcp_post(
            secured_client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert denied.status_code == 401

        allowed = _mcp_post(
            secured_client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Authorization": "Bearer mcp-secret"},
        )
        assert allowed.status_code == 200
        # Non-MCP routes are not affected by the MCP token.
        assert secured_client.get("/api/health").status_code == 200

    # No token and no explicit opt-out: a token is generated, persisted, and enforced.
    default_cfg = server.WebServerConfig(**{**cfg.__dict__, "mcp_allow_no_auth": False})
    default_app = server.create_asgi_app(default_cfg, start_background_tasks=False)
    token_file = tmp_path / "data" / "mcp-token"
    generated = token_file.read_text(encoding="utf-8").strip()
    assert generated
    assert (token_file.stat().st_mode & 0o777) == 0o600
    with TestClient(default_app) as default_client:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert _mcp_post(default_client, payload).status_code == 401
        ok = _mcp_post(default_client, payload, headers={"Authorization": f"Bearer {generated}"})
        assert ok.status_code == 200
    # Restarting reuses the same token.
    server.create_asgi_app(default_cfg, start_background_tasks=False)
    assert token_file.read_text(encoding="utf-8").strip() == generated


@pytest.mark.anyio
async def test_mcp_tools_end_to_end_courseware_homework_and_downloads(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"session": "init-secret"}), encoding="utf-8")
    downloads_root = tmp_path / "downloads"

    def fake_api_json(self, path: str, referer: str | None = None):
        if path == "/api/login/verify":
            return {"code": 2000000, "data": True}
        if path == "/api/course/athena/courses/study?types=Theory%2CTrain":
            return {"code": 2000000, "data": [{"id": "c1", "name": "操作系统", "term": "2026秋"}]}
        if path == "/api/course/courses/v1/c1":
            return {"code": 2000000, "data": {"name": "操作系统"}}
        if path == "/api/course/chapters/tree_list?courseId=c1":
            return {
                "success": True,
                "data": [
                    {
                        "id": "ch1",
                        "name": "第3章 内存管理",
                        "type": "chapter",
                        "children": [
                            {"id": "n1", "name": "分页机制.pptx", "type": "ppt", "resourceId": "att1", "fileSuffix": "pptx", "fileSize": 1.5},
                            {"id": "n2", "name": "进程调度.ppt", "type": "ppt", "resourceId": "att2", "fileSuffix": "ppt", "fileSize": 2.0},
                        ],
                    }
                ],
            }
        if path == "/api/attachment/attachments/att1/viewUrl":
            return {"success": True, "data": {"url": "https://cdn.example/att1.pptx", "originFileName": "分页机制.pptx", "type": "pptx"}}
        if path == "/api/course/homeworks/published/student?courseId=c1":
            return {
                "success": True,
                "data": [
                    homework_item(publish_id="pub1", sub_status=0),
                    {
                        **homework_item(publish_id="pub2", sub_status=1),
                        "homeworkDTO": {"id": "hw2", "name": "第二章 进程 作业", "subStatus": 1, "status": 1, "totalScore": 100},
                    },
                ],
            }
        if path == "/api/course/homeworkQuestions/student?homeworkId=hw1":
            return {"code": 2000000, "data": [{"id": "q1", "orderNumber": 1}]}
        if path == "/api/course/homeworkQuestions/q1/student/detail":
            return {
                "code": 2000000,
                "data": {
                    "qsnType": 1,
                    "titleText": "<p>什么是缺页中断？</p>",
                    "options": [{"id": "A", "optionContent": "页面不在内存"}],
                    "answer": "A",
                    "userAnswer": "",
                },
            }
        raise AssertionError(f"Unexpected path: {path}")

    def fake_download_bytes(self, url: str, referer: str | None = None) -> bytes:
        assert url == "https://cdn.example/att1.pptx"
        return b"PK\x03\x04fake-pptx-bytes"

    monkeypatch.setattr(EduplusClient, "api_json", fake_api_json)
    monkeypatch.setattr(EduplusClient, "download_bytes", fake_download_bytes)
    monkeypatch.setattr(ppt.time, "sleep", Mock())
    monkeypatch.setattr(homework.time, "sleep", Mock())

    mcp = create_mcp_server(config_file=config_path, downloads_root=downloads_root)

    async def call_data(name: str, arguments: dict) -> dict:
        res = await mcp.call_tool(name, arguments)
        assert not getattr(res, "is_error", False)
        if getattr(res, "structured_content", None) is not None:
            return res.structured_content
        return json.loads(res.content[0].text)

    # 1. configure_session persists to the data dir (not config.json) and verifies validity
    config_res = await call_data("configure_session", {"session": "fresh-secret-token", "course_id": "c1"})
    assert config_res["valid"] is True
    assert config_res["persisted_to_disk"] is True
    assert "fresh-secret-token" not in json.dumps(config_res)
    credentials = tmp_path / "data" / "credentials.json"
    assert json.loads(credentials.read_text(encoding="utf-8"))["session"] == "fresh-secret-token"
    assert (credentials.stat().st_mode & 0o777) == 0o600
    assert json.loads(config_path.read_text(encoding="utf-8"))["session"] == "init-secret"
    # Survives a "restart" (runtime overrides cleared).
    clear_runtime_overrides()
    assert load_config(config_file=config_path, require_course_id=False).session == "fresh-secret-token"

    # 2. list_courses
    courses_res = await call_data("list_courses", {})
    assert courses_res["count"] == 1
    assert courses_res["items"][0]["id"] == "c1"

    # 3. list_courseware with keyword filter returns signed_url without downloading
    cw_res = await call_data("list_courseware", {"course_id": "c1", "keyword": "分页"})
    assert cw_res["matched_count"] == 1
    assert cw_res["items"][0]["signed_url"] == "https://cdn.example/att1.pptx"

    # 4. download_courseware saves matching slide to downloads_root
    dl_res = await call_data("download_courseware", {"course_id": "c1", "keyword": "分页"})
    assert len(dl_res["downloaded_files"]) == 1
    saved_ppt = Path(dl_res["downloaded_files"][0]["path"])
    assert saved_ppt.exists()

    # 5. fetch_homework returns Markdown text directly
    hw_res = await call_data(
        "fetch_homework",
        {"course_id": "c1", "status_mode": "undone", "homework_query": "引论", "answer_mode": "answers"},
    )
    assert hw_res["matched_count"] == 1
    assert len(hw_res["markdown_contents"]) == 1
    assert "什么是缺页中断？" in hw_res["markdown_contents"][0]["content"]
    assert "正确答案" in hw_res["markdown_contents"][0]["content"]

    # 6. list_downloads + read_download + path traversal protection
    list_res = await call_data("list_downloads", {})
    assert list_res["count"] >= 3
    md_rel = hw_res["markdown_contents"][0]["relative_path"]
    read_res = await call_data("read_download", {"relative_path": md_rel})
    assert "什么是缺页中断？" in read_res["content"]

    with pytest.raises(Exception):
        await call_data("read_download", {"relative_path": "../../etc/passwd"})


@pytest.mark.anyio
async def test_mcp_file_tools_hide_server_internal_directories(tmp_path) -> None:
    downloads_root = tmp_path / "downloads"
    (downloads_root / "web-jobs" / "job1").mkdir(parents=True)
    (downloads_root / "web-jobs" / "job1" / "other-user.md").write_text("private", encoding="utf-8")
    (downloads_root / "status").mkdir()
    (downloads_root / "status" / "telegram-state.json").write_text("{}", encoding="utf-8")
    (downloads_root / "homework").mkdir()
    (downloads_root / "homework" / "mine.md").write_text("mine", encoding="utf-8")

    mcp = create_mcp_server(
        config_file=tmp_path / "config.json",
        downloads_root=downloads_root,
        hidden_paths=[downloads_root / "web-jobs", downloads_root / "status"],
    )

    listed = await mcp.call_tool("list_downloads", {})
    listed_paths = {item["relative_path"] for item in listed.structured_content["files"]}
    assert listed_paths == {"homework/mine.md"}

    for rel in ("web-jobs/job1/other-user.md", "status/telegram-state.json"):
        with pytest.raises(Exception):
            await mcp.call_tool("read_download", {"relative_path": rel})
    with pytest.raises(Exception):
        await mcp.call_tool("list_downloads", {"subpath": "web-jobs"})


def test_cli_ppt_json_and_session(monkeypatch, tmp_path, capsys) -> None:
    from importlib import import_module

    cli = import_module("eduplus_tools.cli.main")

    def fake_api_json(self, path: str, referer: str | None = None):
        if path == "/api/login/verify":
            return {"code": 2000000}
        if path == "/api/course/courses/v1/c1":
            return {"code": 2000000, "data": {"name": "计算机网络"}}
        if path == "/api/course/chapters/tree_list?courseId=c1":
            return {
                "success": True,
                "data": [{"id": "n1", "name": "第1章 概述.pptx", "type": "ppt", "resourceId": "att1", "fileSuffix": "pptx"}],
            }
        if path == "/api/attachment/attachments/att1/viewUrl":
            return {"success": True, "data": {"url": "https://cdn.example/ch1.pptx", "originFileName": "第1章 概述.pptx", "type": "pptx"}}
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(EduplusClient, "api_json", fake_api_json)
    monkeypatch.setattr(ppt.time, "sleep", Mock())

    monkeypatch.setattr(sys, "argv", [
        "eduplus_tools", "session", "--config", str(tmp_path / "missing.json"), "--session", "secret-123", "--json",
    ])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    monkeypatch.setattr(sys, "argv", [
        "eduplus_tools", "ppt", "--config", str(tmp_path / "missing.json"), "--session", "secret-123",
        "--course-id", "c1", "--keyword", "概述", "--dry-run", "--json",
    ])
    assert cli.main() == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["ppt"]["items"][0]["signed_url"] == "https://cdn.example/ch1.pptx"
