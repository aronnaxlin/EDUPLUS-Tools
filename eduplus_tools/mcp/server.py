from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp, Receive, Scope, Send

from ..core.client import EduplusClient
from ..core.config import (
    data_dir,
    inspect_config_defaults,
    load_config,
    mask_value,
    save_persisted_credentials,
    set_runtime_overrides,
    write_private_file,
)
from ..core.times import CHINA_TZ
from ..features.homework import scrape_homework_detailed
from ..features.ppt import download_ppt_detailed, inspect_courseware
from ..features.status import (
    TASK_STATES,
    TASK_TYPES,
    check_session as fetch_session_check,
    course_is_active,
    course_to_api,
    fetch_courses,
    fetch_status,
    filter_tasks,
    task_to_api,
)
from ..monitor.service import StatusMonitor


READ_ONLY_TOOL = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITE_LOCAL_TOOL = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
MCP_TOKEN_FILENAME = "mcp-token"


def build_transport_security(allowed_hosts_raw: str | None = None) -> TransportSecuritySettings:
    """Configure DNS rebinding protection (Host/Origin header checks).

    Loopback hosts are always allowed. Add the public hostname of a tunnel or reverse
    proxy via EDUPLUS_MCP_ALLOWED_HOSTS (comma-separated, e.g. "mcp.example.com").
    Only the explicit value "*" disables the check.
    """
    raw = allowed_hosts_raw if allowed_hosts_raw is not None else os.getenv("EDUPLUS_MCP_ALLOWED_HOSTS", "")
    extra = [item.strip() for item in raw.split(",") if item.strip()]
    if "*" in extra:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts: list[str] = []
    origins: list[str] = []
    for host in [*LOOPBACK_HOSTS, *extra]:
        hosts.extend([host, f"{host}:*"])
        origins.extend([f"http://{host}", f"http://{host}:*", f"https://{host}", f"https://{host}:*"])
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


def resolve_mcp_token(token: str | None, *, allow_no_auth: bool = False, log: Callable[[str], None] = print) -> str:
    """Return the Bearer token MCP HTTP requests must present, or "" when auth is disabled.

    Order: explicit token > explicit opt-out > token file in the data dir, generated on
    first start so restarts keep the same token.
    """
    cleaned = (token or "").strip()
    if cleaned:
        return cleaned
    if allow_no_auth:
        log("MCP 鉴权：已关闭（EDUPLUS_MCP_ALLOW_NO_AUTH），请确认上游已做访问控制。")
        return ""
    token_path = data_dir() / MCP_TOKEN_FILENAME
    try:
        existing = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        log(f"MCP 鉴权：使用 {token_path} 中的 Bearer token。")
        return existing
    generated = secrets.token_urlsafe(32)
    try:
        write_private_file(token_path, generated + "\n")
    except OSError as exc:
        log(f"MCP 鉴权：无法写入 {token_path}（{exc}），本次使用临时 token，重启后会变化：{generated}")
        return generated
    log(f"MCP 鉴权：已生成 Bearer token 并保存到 {token_path}：{generated}")
    return generated


class BearerTokenMiddleware:
    """Pure ASGI middleware requiring `Authorization: Bearer <token>` on HTTP requests.

    path_prefix limits the check to one mount (e.g. "/mcp"); None protects every path.
    Pure ASGI (not BaseHTTPMiddleware) so streaming MCP responses pass through untouched.
    """

    def __init__(self, app: ASGIApp, *, token: str, path_prefix: str | None = None) -> None:
        self._app = app
        self._token = token.encode()
        self._prefix = path_prefix.rstrip("/") if path_prefix else None

    def _protects(self, path: str) -> bool:
        if self._prefix is None:
            return True
        return path == self._prefix or path.startswith(f"{self._prefix}/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._token or not self._protects(scope.get("path", "")):
            await self._app(scope, receive, send)
            return
        authorization = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                authorization = value.decode("latin-1")
                break
        scheme, _, provided = authorization.partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(provided.strip().encode(), self._token):
            await self._app(scope, receive, send)
            return
        body = json.dumps({"error": "缺少或错误的 MCP 访问 token", "code": "unauthorized"}, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def default_hidden_paths() -> list[Path]:
    """Server-internal directories under downloads that MCP file tools must not expose."""
    status_db = os.getenv("EDUPLUS_STATUS_DB", "downloads/status/status.sqlite3")
    return [
        Path(os.getenv("EDUPLUS_PUBLIC_OUTPUT_ROOT", "downloads/web-jobs")),
        Path(os.getenv("EDUPLUS_BUNDLE_ROOT", "downloads/web-bundles")),
        Path(status_db).parent,
        data_dir(),
    ]


def create_mcp_server(
    *,
    config_file: str | Path = "config.json",
    downloads_root: str | Path | None = None,
    monitor: StatusMonitor | None = None,
    monitor_getter: Callable[[], StatusMonitor | None] | None = None,
    hidden_paths: Iterable[str | Path] | None = None,
) -> MCPServer:
    """Create the generic MCP server exposing EDUPLUS-Tools capabilities.

    hidden_paths are directories (public web jobs, bundles, status DB, data dir) that the
    file tools refuse to read, list, or write even when they sit inside downloads_root.
    """
    resolved_downloads_root = Path(
        downloads_root
        or os.getenv("EDUPLUS_LOCAL_OUTPUT_ROOT")
        or "downloads"
    )
    base_root = resolved_downloads_root.resolve()
    hidden_roots = [
        resolved
        for resolved in (Path(item).resolve() for item in (default_hidden_paths() if hidden_paths is None else hidden_paths))
        # Never hide the downloads root itself (or an ancestor of it).
        if resolved != base_root and resolved not in base_root.parents
    ]

    def _is_hidden(path: Path) -> bool:
        return any(path == hidden or hidden in path.parents for hidden in hidden_roots)

    mcp = MCPServer(
        name="EDUPLUS-Tools",
        title="EDUPLUS Tools MCP Server",
        description=(
            "Query EDUPLUS (U+) courses, homework and experiment deadlines, "
            "search and download courseware (PPT/PPTX by default, PDF and other attachments via file_types), fetch homework questions/answers, "
            "and inspect downloaded files."
        ),
        instructions=(
            "Use list_courses first to discover course IDs when the user refers to a course by name. "
            "Use list_tasks for deadlines and submission status, list_courseware / download_courseware for slides and other courseware files, "
            "and fetch_homework to export and read homework Markdown directly."
        ),
    )

    def _load_client(*, require_course_id: bool = False, course_id: str | None = None, output: Path | None = None) -> EduplusClient:
        try:
            config = load_config(
                config_file=config_file,
                course_id=course_id,
                output=str(output) if output is not None else None,
                require_course_id=require_course_id,
            )
        except SystemExit as exc:
            raise RuntimeError(str(exc)) from exc
        return EduplusClient(config)

    def _resolve_downloads_dir(output_dir: str | None = None) -> Path:
        base = resolved_downloads_root.resolve()
        if not output_dir or not output_dir.strip():
            return base
        candidate = Path(output_dir.strip())
        if not candidate.is_absolute():
            candidate = (base / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate != base and base not in candidate.parents:
            raise ValueError("output_dir must stay within the configured downloads directory")
        if _is_hidden(candidate):
            raise ValueError("output_dir points to a server-internal directory")
        return candidate

    def _safe_download_file(relative_path: str) -> Path:
        base = resolved_downloads_root.resolve()
        cleaned = relative_path.strip().lstrip("/")
        if not cleaned:
            raise ValueError("relative_path must not be empty")
        target = (base / cleaned).resolve()
        if base not in target.parents:
            raise ValueError("relative_path escapes the downloads directory")
        if _is_hidden(target):
            raise ValueError("relative_path points to a server-internal directory")
        return target

    def _active_monitor() -> StatusMonitor | None:
        return monitor_getter() if monitor_getter is not None else monitor

    @mcp.tool(annotations=READ_ONLY_TOOL)
    async def check_session() -> dict[str, Any]:
        """Check whether the currently configured EDUPLUS session (AccessToken) is valid."""
        def _run() -> dict[str, Any]:
            client = _load_client(require_course_id=False)
            result = fetch_session_check(client)
            info = inspect_config_defaults(config_file)
            return {
                "valid": result.valid,
                "upstream_code": result.upstream_code,
                "checked_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
                "session_masked": mask_value(client.config.session),
                "config_path": info.get("config_path", ""),
            }

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=WRITE_LOCAL_TOOL)
    async def configure_session(
        session: str,
        course_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Update the EDUPLUS AccessToken (session) and optional default course_id for this server.

        With persist=true the values are saved to the server data directory and survive restarts
        (until the operator changes EDUPLUS_SESSION, which then takes precedence again).
        """
        cleaned_session = session.strip()
        if not cleaned_session:
            raise ValueError("session must not be empty")

        def _run() -> dict[str, Any]:
            set_runtime_overrides(session=cleaned_session, course_id=course_id)
            target_path: Path | None = None
            persisted = False
            persist_error: str | None = None
            if persist:
                target_path, persisted, persist_error = save_persisted_credentials(
                    session=cleaned_session,
                    course_id=course_id.strip() if course_id is not None else None,
                )

            client = _load_client(require_course_id=False)
            verification = fetch_session_check(client)
            return {
                "updated": True,
                "persisted_to_disk": persisted,
                "persisted_path": str(target_path) if target_path else "",
                "persist_error": persist_error,
                "session_masked": mask_value(cleaned_session),
                "valid": verification.valid,
                "upstream_code": verification.upstream_code,
            }

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=READ_ONLY_TOOL)
    async def list_courses(all_courses: bool = False) -> dict[str, Any]:
        """List EDUPLUS courses with their course_id, name, term, and active status."""
        def _run() -> dict[str, Any]:
            client = _load_client(require_course_id=False)
            now = datetime.now(CHINA_TZ)
            courses = [
                course
                for course in fetch_courses(client)
                if all_courses or course_is_active(course, now)
            ]
            return {
                "fetched_at": now.isoformat(timespec="seconds"),
                "count": len(courses),
                "items": [course_to_api(course, now) for course in courses],
            }

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=READ_ONLY_TOOL)
    async def list_tasks(
        course_id: str | None = None,
        task_type: Literal["homework", "experiment"] | None = None,
        state: Literal["not_submitted", "submitted", "graded"] | None = None,
        overdue: bool | None = None,
        due_within_hours: float | None = None,
        all_courses: bool = False,
    ) -> dict[str, Any]:
        """Query homework and experiment submission status and deadlines across EDUPLUS courses."""
        if task_type is not None and task_type not in TASK_TYPES:
            raise ValueError(f"task_type must be one of {TASK_TYPES}")
        if state is not None and state not in TASK_STATES:
            raise ValueError(f"state must be one of {TASK_STATES}")
        if due_within_hours is not None and due_within_hours <= 0:
            raise ValueError("due_within_hours must be positive")

        def _run() -> dict[str, Any]:
            client = _load_client(require_course_id=False)
            now = datetime.now(CHINA_TZ)
            result = fetch_status(client)
            selected = filter_tasks(
                result.tasks,
                result.courses,
                now,
                types=(task_type,) if task_type else (),
                states=(state,) if state else (),
                course_ids={course_id.strip()} if course_id and course_id.strip() else (),
                active_courses_only=not all_courses,
                overdue=overdue,
                due_after=now if due_within_hours is not None else None,
                due_before=now + timedelta(hours=due_within_hours) if due_within_hours is not None else None,
            )
            return {
                "fetched_at": now.isoformat(timespec="seconds"),
                "count": len(selected),
                "items": [task_to_api(task, now) for task in selected],
                "partial_failures": [asdict(failure) for failure in result.failures],
            }

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=READ_ONLY_TOOL)
    async def list_courseware(
        course_id: str,
        keyword: str | None = None,
        include_urls: bool = True,
        file_types: list[str] | str | None = None,
    ) -> dict[str, Any]:
        """List courseware for a course without downloading files, optionally returning signed URLs.

        file_types defaults to ["ppt", "pptx"]; pass e.g. ["pdf"] or "pdf,pptx", or "*" for every attachment.
        When nothing matches, other_file_types counts the other attachment types found in the chapter tree.
        """
        cleaned_course_id = course_id.strip()
        if not cleaned_course_id:
            raise ValueError("course_id must not be empty")

        def _run() -> dict[str, Any]:
            client = _load_client(require_course_id=False, course_id=cleaned_course_id)
            logs: list[str] = []
            payload = inspect_courseware(
                client,
                course_id=cleaned_course_id,
                keyword=keyword,
                include_urls=include_urls,
                file_types=file_types,
                log=logs.append,
            )
            payload["logs"] = logs
            return payload

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=WRITE_LOCAL_TOOL)
    async def download_courseware(
        course_id: str,
        keyword: str | None = None,
        overwrite: bool = False,
        output_dir: str | None = None,
        file_types: list[str] | str | None = None,
    ) -> dict[str, Any]:
        """Download courseware files for a course into the server downloads directory.

        file_types defaults to ["ppt", "pptx"]; pass e.g. ["pdf"] or "*" for every attachment type.
        """
        cleaned_course_id = course_id.strip()
        if not cleaned_course_id:
            raise ValueError("course_id must not be empty")

        def _run() -> dict[str, Any]:
            target_root = _resolve_downloads_dir(output_dir)
            client = _load_client(require_course_id=False, course_id=cleaned_course_id, output=target_root)
            logs: list[str] = []
            result = download_ppt_detailed(
                client,
                course_id=cleaned_course_id,
                course_name=client.config.course_name or None,
                output_root=target_root,
                dry_run=False,
                overwrite=overwrite,
                keyword=keyword,
                file_types=file_types,
                log=logs.append,
            )
            base = resolved_downloads_root.resolve()
            for item in result["downloaded_files"]:
                path_obj = Path(item["path"]).resolve()
                if base in path_obj.parents:
                    item["relative_path"] = path_obj.relative_to(base).as_posix()
            result["logs"] = logs
            return result

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=WRITE_LOCAL_TOOL)
    async def fetch_homework(
        course_id: str,
        status_mode: Literal["all", "done", "undone"] = "all",
        answer_mode: Literal["plain", "answers", "both"] = "plain",
        homework_query: str | None = None,
        include_content: bool = True,
        max_content_chars: int = 80000,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Fetch homework questions (and optional answers) for a course, save JSON/Markdown, and return Markdown text."""
        cleaned_course_id = course_id.strip()
        if not cleaned_course_id:
            raise ValueError("course_id must not be empty")

        def _run() -> dict[str, Any]:
            target_root = _resolve_downloads_dir(output_dir)
            client = _load_client(require_course_id=False, course_id=cleaned_course_id, output=target_root)
            logs: list[str] = []
            result = scrape_homework_detailed(
                client,
                course_id=cleaned_course_id,
                output_root=target_root,
                convert_existing=False,
                answer_mode=answer_mode,
                status_mode=status_mode,
                homework_query=homework_query,
                log=logs.append,
            )
            base = resolved_downloads_root.resolve()
            markdown_contents: list[dict[str, Any]] = []
            remaining = max(0, int(max_content_chars))
            if include_content:
                for raw_path in result["markdown_files"]:
                    md_path = Path(raw_path).resolve()
                    rel = md_path.relative_to(base).as_posix() if base in md_path.parents else str(md_path)
                    text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
                    truncated = len(text) > remaining
                    snippet = text[:remaining]
                    remaining = max(0, remaining - len(snippet))
                    markdown_contents.append(
                        {
                            "path": str(md_path),
                            "relative_path": rel,
                            "content": snippet,
                            "truncated": truncated,
                        }
                    )
            result["markdown_contents"] = markdown_contents
            result["logs"] = logs
            return result

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=READ_ONLY_TOOL)
    async def list_downloads(subpath: str = "", limit: int = 200) -> dict[str, Any]:
        """List files currently saved under the server downloads directory."""
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        def _run() -> dict[str, Any]:
            base = resolved_downloads_root.resolve()
            target = base
            cleaned = subpath.strip().lstrip("/")
            if cleaned:
                target = (base / cleaned).resolve()
                if target != base and base not in target.parents:
                    raise ValueError("subpath escapes the downloads directory")
                if _is_hidden(target):
                    raise ValueError("subpath points to a server-internal directory")

            if not target.exists():
                return {"root": str(base), "count": 0, "truncated": False, "files": []}

            all_files = sorted(
                (p for p in target.rglob("*") if p.is_file() and not _is_hidden(p.resolve())),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            truncated = len(all_files) > limit
            items = []
            for file_path in all_files[:limit]:
                stat = file_path.stat()
                items.append(
                    {
                        "relative_path": file_path.relative_to(base).as_posix(),
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=CHINA_TZ).isoformat(timespec="seconds"),
                    }
                )
            return {
                "root": str(base),
                "count": len(items),
                "truncated": truncated,
                "files": items,
            }

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=READ_ONLY_TOOL)
    async def read_download(relative_path: str, max_chars: int = 80000) -> dict[str, Any]:
        """Read a downloaded text, Markdown, or JSON file from the server downloads directory."""
        if max_chars < 1:
            raise ValueError("max_chars must be positive")

        def _run() -> dict[str, Any]:
            target = _safe_download_file(relative_path)
            if not target.exists() or not target.is_file():
                raise FileNotFoundError(f"File not found: {relative_path}")
            raw = target.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{relative_path} is a binary file ({len(raw)} bytes) and cannot be read as text") from exc
            truncated = len(text) > max_chars
            return {
                "relative_path": target.relative_to(resolved_downloads_root.resolve()).as_posix(),
                "size_bytes": len(raw),
                "truncated": truncated,
                "content": text[:max_chars],
            }

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=READ_ONLY_TOOL)
    async def get_status_monitor(include_tasks: bool = False, event_limit: int = 20) -> dict[str, Any]:
        """Read the background StatusMonitor snapshot metadata and recent change events if enabled."""
        def _run() -> dict[str, Any]:
            active = _active_monitor()
            if active is None:
                return {"enabled": False, "message": "StatusMonitor is not active in this process."}
            latest_scan = active.latest_scan()
            snapshot = active.snapshot()
            events, next_cursor = active.store.list_events(None, max(1, min(event_limit, 200)))
            now = active.now()
            payload: dict[str, Any] = {
                "enabled": True,
                "latest_scan": latest_scan,
                "has_snapshot": snapshot is not None,
                "scanned_at": snapshot.scanned_at if snapshot else None,
                "course_count": len(snapshot.courses) if snapshot else 0,
                "task_count": len(snapshot.tasks) if snapshot else 0,
                "events": events,
                "next_cursor": next_cursor,
            }
            if include_tasks and snapshot is not None:
                selected = filter_tasks(snapshot.tasks.values(), snapshot.courses, now)
                payload["tasks"] = [task_to_api(task, now) for task in selected]
            return payload

        return await anyio.to_thread.run_sync(_run)

    @mcp.tool(annotations=WRITE_LOCAL_TOOL)
    async def trigger_status_scan() -> dict[str, Any]:
        """Trigger an immediate status scan on the background StatusMonitor (or run a direct status check)."""
        def _run() -> dict[str, Any]:
            active = _active_monitor()
            if active is not None:
                return {"mode": "monitor", "scan": active.scan_now("mcp")}
            client = _load_client(require_course_id=False)
            now = datetime.now(CHINA_TZ)
            result = fetch_status(client)
            return {
                "mode": "direct",
                "fetched_at": now.isoformat(timespec="seconds"),
                "course_count": len(result.courses),
                "task_count": len(result.tasks),
                "partial_failures": [asdict(failure) for failure in result.failures],
            }

        return await anyio.to_thread.run_sync(_run)

    @mcp.resource("eduplus://courses", mime_type="application/json")
    async def courses_resource() -> str:
        """Current active EDUPLUS courses as JSON."""
        data = await list_courses(all_courses=False)
        return json.dumps(data, ensure_ascii=False, indent=2)

    @mcp.resource("eduplus://tasks", mime_type="application/json")
    async def tasks_resource() -> str:
        """Current active-course homework and experiment tasks as JSON."""
        data = await list_tasks(all_courses=False)
        return json.dumps(data, ensure_ascii=False, indent=2)

    @mcp.resource("eduplus://downloads/{relative_path}", mime_type="text/plain")
    async def download_resource(relative_path: str) -> str:
        """Read a downloaded text/Markdown/JSON file from the downloads directory."""
        data = await read_download(relative_path=relative_path)
        return str(data["content"])

    return mcp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EDUPLUS-Tools MCP Server")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "sse", "stdio"],
        default=os.getenv("EDUPLUS_MCP_TRANSPORT", "streamable-http"),
        help="MCP transport mode. Default: streamable-http",
    )
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"), help="Listen host for HTTP transports. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8001")), help="Listen port for HTTP transports")
    parser.add_argument("--path", default=os.getenv("EDUPLUS_MCP_PATH", "/mcp"), help="HTTP route path for streamable-http")
    parser.add_argument("--config", default=os.getenv("EDUPLUS_STATUS_CONFIG", "config.json"), help="Config file path")
    parser.add_argument("--downloads-root", default=os.getenv("EDUPLUS_LOCAL_OUTPUT_ROOT", "downloads"), help="Downloads directory")
    parser.add_argument("--allowed-hosts", default=os.getenv("EDUPLUS_MCP_ALLOWED_HOSTS", ""), help="Extra allowed Host headers (comma-separated); loopback is always allowed; * disables the check")
    parser.add_argument("--token", default=os.getenv("EDUPLUS_MCP_TOKEN", ""), help="Bearer token for HTTP transports; generated into the data dir when empty")
    parser.add_argument(
        "--allow-no-auth",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("EDUPLUS_MCP_ALLOW_NO_AUTH", "").strip().lower() in {"1", "true", "yes", "on"},
        help="Serve HTTP transports without a Bearer token (only behind an authenticating proxy)",
    )
    return parser


def run_mcp_server(
    *,
    transport: str = "streamable-http",
    host: str = "127.0.0.1",
    port: int = 8001,
    path: str = "/mcp",
    config_file: str | Path = "config.json",
    downloads_root: str | Path | None = None,
    allowed_hosts: str | None = None,
    token: str | None = None,
    allow_no_auth: bool | None = None,
) -> int:
    mcp = create_mcp_server(config_file=config_file, downloads_root=downloads_root)
    if transport == "stdio":
        mcp.run(transport="stdio")
        return 0

    import uvicorn

    if allow_no_auth is None:
        allow_no_auth = os.getenv("EDUPLUS_MCP_ALLOW_NO_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}
    if token is None:
        token = os.getenv("EDUPLUS_MCP_TOKEN", "")
    log = lambda message: print(message, file=sys.stderr)  # noqa: E731 - keep stdout clean
    resolved_token = resolve_mcp_token(token, allow_no_auth=allow_no_auth, log=log)
    security = build_transport_security(allowed_hosts)
    if transport == "sse":
        app: ASGIApp = mcp.sse_app(transport_security=security, host=host)
    else:
        app = mcp.streamable_http_app(
            streamable_http_path=path,
            stateless_http=True,
            transport_security=security,
            host=host,
        )
    if resolved_token:
        app = BearerTokenMiddleware(app, token=resolved_token)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    return run_mcp_server(
        transport=args.transport,
        host=args.host,
        port=args.port,
        path=args.path,
        config_file=args.config,
        downloads_root=args.downloads_root,
        allowed_hosts=args.allowed_hosts,
        token=args.token,
        allow_no_auth=args.allow_no_auth,
    )
