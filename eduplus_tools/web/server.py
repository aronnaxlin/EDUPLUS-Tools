from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import ParseResult, urlparse

import anyio
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..core.client import EduplusClient
from ..core.config import data_dir, inspect_config_defaults, load_config
from ..mcp.server import BearerTokenMiddleware, build_transport_security, create_mcp_server, resolve_mcp_token
from ..monitor.service import EduplusUpstream, StatusMonitor
from ..monitor.store import StatusStore
from .jobs import (
    JobStore,
    build_job_bundle,
    cleanup_job_artifacts,
    cleanup_job_bundle,
    configure_storage,
    list_job_artifacts,
    run_job_async,
    serialize_job,
)
from .status_api import StatusApi


WEB_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = WEB_ROOT / "static"
JOB_STORE = JobStore()
SERVER_CONFIG: "WebServerConfig | None" = None
STATUS_API: StatusApi | None = None
STATUS_MONITOR: StatusMonitor | None = None


@dataclass(frozen=True)
class WebServerConfig:
    host: str
    port: int
    enable_local_output: bool
    auto_delete_public_downloads: bool
    public_job_ttl_seconds: int
    cleanup_interval_seconds: int
    public_output_root: Path
    bundle_root: Path
    local_output_root: Path
    status_token: str
    status_db: Path
    status_scan_interval_seconds: int
    status_config: str
    mcp_enabled: bool = True
    mcp_path: str = "/mcp"
    mcp_token: str = ""
    mcp_allowed_hosts: str = ""
    mcp_allow_no_auth: bool = False
    web_ui_enabled: bool = True


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EDUPLUS 网页界面与 MCP 服务")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"), help="监听地址")
    parser.add_argument("--port", type=int, default=env_int("PORT", 8000), help="监听端口")
    parser.add_argument(
        "--enable-local-output",
        action=argparse.BooleanOptionalAction,
        default=env_bool("EDUPLUS_ENABLE_LOCAL_OUTPUT", True),
        help="允许在网页界面中使用本地输出模式（默认开启）",
    )
    parser.add_argument(
        "--auto-delete-public-downloads",
        action=argparse.BooleanOptionalAction,
        default=env_bool("EDUPLUS_AUTO_DELETE_PUBLIC_DOWNLOADS", False),
        help="下载 ZIP 后是否自动删除公共模式任务文件（默认关闭）",
    )
    parser.add_argument("--public-job-ttl-seconds", type=int, default=env_int("EDUPLUS_PUBLIC_JOB_TTL_SECONDS", 1800), help="公共模式任务文件保留时长")
    parser.add_argument("--cleanup-interval-seconds", type=int, default=env_int("EDUPLUS_CLEANUP_INTERVAL_SECONDS", 60), help="后台清理间隔")
    parser.add_argument("--public-output-root", default=os.getenv("EDUPLUS_PUBLIC_OUTPUT_ROOT", "downloads/web-jobs"), help="公共模式任务隔离目录")
    parser.add_argument("--bundle-root", default=os.getenv("EDUPLUS_BUNDLE_ROOT", "downloads/web-bundles"), help="ZIP 打包输出目录")
    parser.add_argument("--local-output-root", default=os.getenv("EDUPLUS_LOCAL_OUTPUT_ROOT", "downloads"), help="本地输出模式默认目录")
    parser.add_argument("--status-token", default=os.getenv("EDUPLUS_STATUS_TOKEN", ""), help="状态检测 API 的访问 token；留空则不启用")
    parser.add_argument("--status-db", default=os.getenv("EDUPLUS_STATUS_DB", "downloads/status/status.sqlite3"), help="状态检测快照和事件的存储位置")
    parser.add_argument("--status-scan-interval-seconds", type=int, default=env_int("EDUPLUS_STATUS_SCAN_INTERVAL_SECONDS", 600), help="状态检测扫描间隔，最少 60 秒")
    parser.add_argument("--status-config", default=os.getenv("EDUPLUS_STATUS_CONFIG", "config.json"), help="状态检测与 MCP 读取 SESSION 的配置文件")
    parser.add_argument(
        "--web-ui",
        dest="web_ui_enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("EDUPLUS_WEB_UI_ENABLED", True),
        help="是否提供网页界面及其任务接口（/、/static、/api/config、/api/run、/api/jobs）；公网部署建议关闭，只保留 MCP 与状态 API",
    )
    parser.add_argument(
        "--mcp-enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("EDUPLUS_MCP_ENABLED", True),
        help="是否在同端口启用 MCP Streamable HTTP 服务（默认开启）",
    )
    parser.add_argument("--mcp-path", default=os.getenv("EDUPLUS_MCP_PATH", "/mcp"), help="MCP HTTP 挂载路径，默认 /mcp")
    parser.add_argument("--mcp-token", default=os.getenv("EDUPLUS_MCP_TOKEN", ""), help="MCP Bearer Token；留空时自动生成并保存到数据目录的 mcp-token 文件")
    parser.add_argument(
        "--mcp-allow-no-auth",
        action=argparse.BooleanOptionalAction,
        default=env_bool("EDUPLUS_MCP_ALLOW_NO_AUTH", False),
        help="不校验 MCP Bearer Token（仅在上游反代/Tunnel 已做访问控制时使用）",
    )
    parser.add_argument("--mcp-allowed-hosts", default=os.getenv("EDUPLUS_MCP_ALLOWED_HOSTS", ""), help="额外允许的 Host（逗号分隔，如 Tunnel 域名）；本机地址始终允许；* 表示关闭检查")
    return parser


def load_server_config(args: argparse.Namespace) -> WebServerConfig:
    mcp_path = "/" + str(args.mcp_path or "/mcp").strip().strip("/")
    return WebServerConfig(
        host=args.host,
        port=args.port,
        enable_local_output=bool(args.enable_local_output),
        auto_delete_public_downloads=bool(args.auto_delete_public_downloads),
        public_job_ttl_seconds=max(0, int(args.public_job_ttl_seconds)),
        cleanup_interval_seconds=max(5, int(args.cleanup_interval_seconds)),
        public_output_root=Path(args.public_output_root),
        bundle_root=Path(args.bundle_root),
        local_output_root=Path(args.local_output_root),
        status_token=str(args.status_token or "").strip(),
        status_db=Path(args.status_db),
        status_scan_interval_seconds=max(60, int(args.status_scan_interval_seconds)),
        status_config=str(args.status_config),
        mcp_enabled=bool(args.mcp_enabled),
        mcp_path=mcp_path,
        mcp_token=str(args.mcp_token or "").strip(),
        mcp_allowed_hosts=str(args.mcp_allowed_hosts or "").strip(),
        mcp_allow_no_auth=bool(args.mcp_allow_no_auth),
        web_ui_enabled=bool(args.web_ui_enabled),
    )


class WebHandler(BaseHTTPRequestHandler):
    """Standard-library HTTP handler kept for direct unit tests and simple embeds."""

    server_version = "EduplusWeb/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/"):
            self._handle_status_api("GET", parsed)
            return
        if parsed.path == "/":
            self._serve_file(STATIC_ROOT / "index.html")
            return
        if parsed.path == "/api/health":
            self._send_json(_health_payload())
            return
        if parsed.path == "/api/config":
            self._send_json(_config_payload())
            return
        if parsed.path.startswith("/api/jobs/"):
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 3:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            job_id = parts[2]
            job = JOB_STORE.get(job_id)
            if job is None:
                self._send_json({"error": "未找到任务"}, status=HTTPStatus.NOT_FOUND)
                return
            if len(parts) == 4 and parts[3] == "artifacts":
                self._send_json(list_job_artifacts(job))
                return
            if len(parts) == 4 and parts[3] == "bundle.zip":
                bundle_path = build_job_bundle(job)
                if bundle_path is None:
                    self._send_json({"error": "当前任务没有可下载文件"}, status=HTTPStatus.NOT_FOUND)
                    return
                cleanup_after_send = _bundle_cleanup_callback(job)
                self._serve_file(bundle_path, download_name=f"eduplus-job-{job.id}.zip", cleanup_after_send=cleanup_after_send)
                return
            if len(parts) != 3:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_json(serialize_job(job))
            return
        if parsed.path.startswith("/static/"):
            relative = parsed.path.removeprefix("/static/")
            target = (STATIC_ROOT / relative).resolve()
            if STATIC_ROOT not in target.parents and target != STATIC_ROOT:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            self._serve_file(target)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/v1/"):
            self._handle_status_api("POST", parsed)
            return
        if parsed.path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json_body()
        except ValueError:
            return
        error_response = _validate_run_payload(payload)
        if error_response is not None:
            body, status = error_response
            self._send_json(body, status=status)
            return

        job = run_job_async(JOB_STORE, payload)
        self._send_json({"job_id": job.id}, status=HTTPStatus.ACCEPTED)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_status_api(self, method: str, parsed: ParseResult) -> None:
        if STATUS_API is None:
            self._send_json({"error": "状态检测 API 未启用，请设置 EDUPLUS_STATUS_TOKEN", "code": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return
        STATUS_API.handle(self, method, parsed, self.headers.get("Authorization"))

    def _serve_file(
        self,
        path: Path,
        download_name: str | None = None,
        cleanup_after_send: Callable[[], None] | None = None,
    ) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(content)
        self.wfile.flush()
        if cleanup_after_send is not None:
            cleanup_after_send()

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": "请求内容不是有效的 JSON"}, status=HTTPStatus.BAD_REQUEST)
            raise ValueError("请求内容不是有效的 JSON")
        if not isinstance(payload, dict):
            self._send_json({"error": "请求内容必须是 JSON 对象"}, status=HTTPStatus.BAD_REQUEST)
            raise ValueError("请求内容必须是 JSON 对象")
        return payload

    def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK, headers: dict[str, str] | None = None) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)


def _health_payload() -> dict[str, object]:
    return {
        "ok": True,
        "web_ui_enabled": SERVER_CONFIG.web_ui_enabled if SERVER_CONFIG else True,
        "mcp_enabled": SERVER_CONFIG.mcp_enabled if SERVER_CONFIG else True,
        "mcp_path": SERVER_CONFIG.mcp_path if SERVER_CONFIG else "/mcp",
    }


def _config_payload() -> dict[str, object]:
    config_file = SERVER_CONFIG.status_config if SERVER_CONFIG else "config.json"
    config_defaults = inspect_config_defaults(config_file)
    return {
        "enable_local_output": SERVER_CONFIG.enable_local_output if SERVER_CONFIG else True,
        "public_output_root": str(SERVER_CONFIG.public_output_root) if SERVER_CONFIG else "downloads/web-jobs",
        "local_output_root": str(SERVER_CONFIG.local_output_root) if SERVER_CONFIG else "downloads",
        "has_server_session": bool(config_defaults.get("has_session")),
        "config_path": str(config_defaults.get("config_path") or ""),
        "mcp_enabled": SERVER_CONFIG.mcp_enabled if SERVER_CONFIG else True,
        "mcp_path": SERVER_CONFIG.mcp_path if SERVER_CONFIG else "/mcp",
    }


def _validate_run_payload(payload: dict[str, object]) -> tuple[dict[str, object], HTTPStatus] | None:
    command = str(payload.get("command") or "all")
    if command not in {"all", "ppt", "homework"}:
        return {"error": "不支持的任务类型"}, HTTPStatus.BAD_REQUEST
    if (
        str(payload.get("execution_mode") or "public").strip().lower() == "local"
        and SERVER_CONFIG
        and not SERVER_CONFIG.enable_local_output
    ):
        return {"error": "当前服务未开启本地输出模式"}, HTTPStatus.FORBIDDEN
    return None


def _bundle_cleanup_callback(job) -> Callable[[], None] | None:
    if SERVER_CONFIG and job.execution_mode == "public" and SERVER_CONFIG.auto_delete_public_downloads:
        return lambda: cleanup_job_artifacts(job, delete_output_root=True)
    if job.execution_mode == "local":
        return lambda: cleanup_job_bundle(job)
    return None


def _file_response(path: Path, *, download_name: str | None = None, cleanup_after_send: Callable[[], None] | None = None) -> Response:
    if not path.exists() or not path.is_file():
        return Response(status_code=HTTPStatus.NOT_FOUND)
    content = path.read_bytes()
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    headers: dict[str, str] = {}
    if download_name:
        headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
    if cleanup_after_send is not None:
        cleanup_after_send()
    return Response(content=content, media_type=content_type, headers=headers)


def create_asgi_app(
    config: WebServerConfig | None = None,
    *,
    start_background_tasks: bool = True,
) -> Starlette:
    """Create the unified ASGI application serving the Web UI, Status API, and Streamable HTTP MCP."""
    active_config = config or SERVER_CONFIG or load_server_config(build_parser().parse_args([]))

    async def home(_: Request) -> Response:
        return await anyio.to_thread.run_sync(lambda: _file_response(STATIC_ROOT / "index.html"))

    async def health(_: Request) -> Response:
        return JSONResponse(_health_payload())

    async def get_config(_: Request) -> Response:
        return JSONResponse(await anyio.to_thread.run_sync(_config_payload))

    async def run_job(request: Request) -> Response:
        try:
            raw = await request.body()
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse({"error": "请求内容不是有效的 JSON"}, status_code=HTTPStatus.BAD_REQUEST)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "请求内容必须是 JSON 对象"}, status_code=HTTPStatus.BAD_REQUEST)
        error_response = _validate_run_payload(payload)
        if error_response is not None:
            body, status = error_response
            return JSONResponse(body, status_code=status)
        job = run_job_async(JOB_STORE, payload)
        return JSONResponse({"job_id": job.id}, status_code=HTTPStatus.ACCEPTED)

    async def job_detail(request: Request) -> Response:
        job_id = request.path_params["job_id"]
        job = JOB_STORE.get(job_id)
        if job is None:
            return JSONResponse({"error": "未找到任务"}, status_code=HTTPStatus.NOT_FOUND)
        return JSONResponse(await anyio.to_thread.run_sync(serialize_job, job))

    async def job_artifacts(request: Request) -> Response:
        job_id = request.path_params["job_id"]
        job = JOB_STORE.get(job_id)
        if job is None:
            return JSONResponse({"error": "未找到任务"}, status_code=HTTPStatus.NOT_FOUND)
        return JSONResponse(await anyio.to_thread.run_sync(list_job_artifacts, job))

    async def job_bundle(request: Request) -> Response:
        job_id = request.path_params["job_id"]
        job = JOB_STORE.get(job_id)
        if job is None:
            return JSONResponse({"error": "未找到任务"}, status_code=HTTPStatus.NOT_FOUND)
        bundle_path = await anyio.to_thread.run_sync(build_job_bundle, job)
        if bundle_path is None:
            return JSONResponse({"error": "当前任务没有可下载文件"}, status_code=HTTPStatus.NOT_FOUND)
        cleanup_after_send = _bundle_cleanup_callback(job)
        return await anyio.to_thread.run_sync(
            lambda: _file_response(
                bundle_path,
                download_name=f"eduplus-job-{job.id}.zip",
                cleanup_after_send=cleanup_after_send,
            )
        )

    async def static_asset(request: Request) -> Response:
        relative = request.path_params["path"]
        target = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT not in target.parents and target != STATIC_ROOT:
            return Response(status_code=HTTPStatus.FORBIDDEN)
        return await anyio.to_thread.run_sync(lambda: _file_response(target))

    async def status_api_endpoint(request: Request) -> Response:
        if STATUS_API is None:
            return JSONResponse(
                {"error": "状态检测 API 未启用，请设置 EDUPLUS_STATUS_TOKEN", "code": "not_found"},
                status_code=HTTPStatus.NOT_FOUND,
            )
        raw_target = request.url.path
        if request.url.query:
            raw_target = f"{raw_target}?{request.url.query}"
        parsed = urlparse(raw_target)
        auth_header = request.headers.get("authorization")
        payload, status, headers = await anyio.to_thread.run_sync(
            STATUS_API.evaluate,
            request.method,
            parsed,
            auth_header,
        )
        return JSONResponse(payload, status_code=status, headers=headers)

    routes: list[Route] = [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/v1/{path:path}", status_api_endpoint, methods=["GET", "POST"]),
    ]
    if active_config.web_ui_enabled:
        # The web UI has no login of its own and its local mode runs jobs with the server
        # session, so deployments reachable from the internet turn it off.
        routes += [
            Route("/", home, methods=["GET"]),
            Route("/api/config", get_config, methods=["GET"]),
            Route("/api/run", run_job, methods=["POST"]),
            Route("/api/jobs/{job_id}", job_detail, methods=["GET"]),
            Route("/api/jobs/{job_id}/artifacts", job_artifacts, methods=["GET"]),
            Route("/api/jobs/{job_id}/bundle.zip", job_bundle, methods=["GET"]),
            Route("/static/{path:path}", static_asset, methods=["GET"]),
        ]

    mcp_server = None
    if active_config.mcp_enabled:
        mcp_server = create_mcp_server(
            config_file=active_config.status_config,
            downloads_root=active_config.local_output_root,
            monitor_getter=lambda: STATUS_MONITOR,
            hidden_paths=[
                active_config.public_output_root,
                active_config.bundle_root,
                active_config.status_db.parent,
                data_dir(),
            ],
        )
        mcp_app = mcp_server.streamable_http_app(
            streamable_http_path=active_config.mcp_path,
            stateless_http=True,
            transport_security=build_transport_security(active_config.mcp_allowed_hosts),
            host=active_config.host,
        )
        routes = [*mcp_app.routes, *routes]

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        if start_background_tasks:
            threading.Thread(target=_cleanup_loop, daemon=True).start()
            if STATUS_MONITOR is not None:
                STATUS_MONITOR.start_scheduler()
        if mcp_server is not None:
            async with mcp_server.session_manager.run():
                yield
        else:
            yield

    app = Starlette(routes=routes, lifespan=lifespan)
    if active_config.mcp_enabled:
        token = resolve_mcp_token(active_config.mcp_token, allow_no_auth=active_config.mcp_allow_no_auth)
        if token:
            app.add_middleware(BearerTokenMiddleware, token=token, path_prefix=active_config.mcp_path)
    return app


def build_status_monitor(config: WebServerConfig) -> StatusMonitor:
    def load_client() -> EduplusClient:
        eduplus_config = load_config(
            config_file=config.status_config,
            require_course_id=False,
        )
        return EduplusClient(eduplus_config)

    return StatusMonitor(
        StatusStore(config.status_db),
        EduplusUpstream(load_client),
        interval_seconds=config.status_scan_interval_seconds,
    )


def build_status_api(config: WebServerConfig, monitor: StatusMonitor | None = None) -> StatusApi | None:
    if not config.status_token:
        return None
    active_monitor = monitor or build_status_monitor(config)
    return StatusApi(active_monitor, config.status_token)


def main() -> int:
    args = build_parser().parse_args()
    global SERVER_CONFIG, STATUS_API, STATUS_MONITOR
    SERVER_CONFIG = load_server_config(args)
    configure_storage(
        public_output_root=SERVER_CONFIG.public_output_root,
        bundle_root=SERVER_CONFIG.bundle_root,
        local_output_root=SERVER_CONFIG.local_output_root,
    )
    STATUS_MONITOR = build_status_monitor(SERVER_CONFIG)
    STATUS_API = build_status_api(SERVER_CONFIG, STATUS_MONITOR)
    app = create_asgi_app(SERVER_CONFIG, start_background_tasks=True)

    print(f"EDUPLUS 服务已启动：http://{SERVER_CONFIG.host}:{SERVER_CONFIG.port}")
    if SERVER_CONFIG.web_ui_enabled:
        print(f"网页界面：已开启；本地输出模式：{'已开启' if SERVER_CONFIG.enable_local_output else '未开启'}")
    else:
        print("网页界面：未开启（仅提供 /api/health、/api/v1 与 MCP）")
    print(f"状态检测 API：{f'已开启，每 {SERVER_CONFIG.status_scan_interval_seconds} 秒扫描一次' if STATUS_API else '未开启（后台扫描已就绪）'}")
    if SERVER_CONFIG.mcp_enabled:
        print(f"MCP Streamable HTTP：http://{SERVER_CONFIG.host}:{SERVER_CONFIG.port}{SERVER_CONFIG.mcp_path}")
    else:
        print("MCP 服务：未开启")

    uvicorn.run(app, host=SERVER_CONFIG.host, port=SERVER_CONFIG.port, log_level="warning")
    return 0


def _cleanup_loop() -> None:
    while True:
        if SERVER_CONFIG is None:
            time.sleep(5)
            continue

        now = time.time()
        for job in JOB_STORE.all():
            if job.execution_mode != "public" or job.cleaned_at is not None:
                continue
            if job.status not in {"completed", "failed"}:
                continue
            finished_at = job.finished_at or job.created_at
            if SERVER_CONFIG.public_job_ttl_seconds and now - finished_at >= SERVER_CONFIG.public_job_ttl_seconds:
                cleanup_job_artifacts(job, delete_output_root=True)

        time.sleep(SERVER_CONFIG.cleanup_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
