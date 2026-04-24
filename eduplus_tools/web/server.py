from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .jobs import JobStore, build_job_bundle, cleanup_job_artifacts, cleanup_job_bundle, configure_storage, list_job_artifacts, run_job_async, serialize_job


WEB_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = WEB_ROOT / "static"
JOB_STORE = JobStore()
SERVER_CONFIG: "WebServerConfig | None" = None


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


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EDUPLUS Tools Web UI")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"), help="Bind host")
    parser.add_argument("--port", type=int, default=env_int("PORT", 8000), help="Bind port")
    parser.add_argument("--enable-local-output", action="store_true", default=env_bool("EDUPLUS_ENABLE_LOCAL_OUTPUT", False), help="Allow local output mode in Web UI")
    parser.add_argument("--auto-delete-public-downloads", action="store_true", default=env_bool("EDUPLUS_AUTO_DELETE_PUBLIC_DOWNLOADS", True), help="Delete public job files after ZIP download")
    parser.add_argument("--public-job-ttl-seconds", type=int, default=env_int("EDUPLUS_PUBLIC_JOB_TTL_SECONDS", 1800), help="Cleanup age for finished public jobs")
    parser.add_argument("--cleanup-interval-seconds", type=int, default=env_int("EDUPLUS_CLEANUP_INTERVAL_SECONDS", 60), help="Background cleanup interval")
    parser.add_argument("--public-output-root", default=os.getenv("EDUPLUS_PUBLIC_OUTPUT_ROOT", "downloads/web-jobs"), help="Base directory for isolated public jobs")
    parser.add_argument("--bundle-root", default=os.getenv("EDUPLUS_BUNDLE_ROOT", "downloads/web-bundles"), help="Directory for generated ZIP bundles")
    parser.add_argument("--local-output-root", default=os.getenv("EDUPLUS_LOCAL_OUTPUT_ROOT", "downloads"), help="Default local output directory")
    return parser


def load_server_config(args: argparse.Namespace) -> WebServerConfig:
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
    )


class WebHandler(BaseHTTPRequestHandler):
    server_version = "EduplusWeb/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_file(STATIC_ROOT / "index.html")
            return
        if parsed.path == "/api/health":
            self._send_json({"ok": True})
            return
        if parsed.path == "/api/config":
            self._send_json(
                {
                    "enable_local_output": SERVER_CONFIG.enable_local_output if SERVER_CONFIG else False,
                    "public_output_root": str(SERVER_CONFIG.public_output_root) if SERVER_CONFIG else "downloads/web-jobs",
                    "local_output_root": str(SERVER_CONFIG.local_output_root) if SERVER_CONFIG else "downloads",
                }
            )
            return
        if parsed.path.startswith("/api/jobs/"):
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) < 3:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            job_id = parts[2]
            job = JOB_STORE.get(job_id)
            if job is None:
                self._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            if len(parts) == 4 and parts[3] == "artifacts":
                self._send_json(list_job_artifacts(job))
                return
            if len(parts) == 4 and parts[3] == "bundle.zip":
                bundle_path = build_job_bundle(job)
                if bundle_path is None:
                    self._send_json({"error": "job has no downloadable artifacts"}, status=HTTPStatus.NOT_FOUND)
                    return
                cleanup_after_send = None
                if SERVER_CONFIG and job.execution_mode == "public" and SERVER_CONFIG.auto_delete_public_downloads:
                    cleanup_after_send = lambda: cleanup_job_artifacts(job, delete_output_root=True)
                elif job.execution_mode == "local":
                    cleanup_after_send = lambda: cleanup_job_bundle(job)
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
        if parsed.path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json_body()
        except ValueError:
            return
        command = str(payload.get("command") or "all")
        if command not in {"all", "ppt", "homework"}:
            self._send_json({"error": "invalid command"}, status=HTTPStatus.BAD_REQUEST)
            return
        if str(payload.get("execution_mode") or "public").strip().lower() == "local" and SERVER_CONFIG and not SERVER_CONFIG.enable_local_output:
            self._send_json({"error": "local output mode is disabled on this server"}, status=HTTPStatus.FORBIDDEN)
            return

        job = run_job_async(JOB_STORE, payload)
        self._send_json({"job_id": job.id}, status=HTTPStatus.ACCEPTED)

    def log_message(self, format: str, *args: object) -> None:
        return

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
            self._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
            raise ValueError("invalid json body")
        if not isinstance(payload, dict):
            self._send_json({"error": "json body must be an object"}, status=HTTPStatus.BAD_REQUEST)
            raise ValueError("json body must be an object")
        return payload

    def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> int:
    args = build_parser().parse_args()
    global SERVER_CONFIG
    SERVER_CONFIG = load_server_config(args)
    configure_storage(
        public_output_root=SERVER_CONFIG.public_output_root,
        bundle_root=SERVER_CONFIG.bundle_root,
        local_output_root=SERVER_CONFIG.local_output_root,
    )
    server = ThreadingHTTPServer((SERVER_CONFIG.host, SERVER_CONFIG.port), WebHandler)
    cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
    cleanup_thread.start()
    print(f"EDUPLUS Web UI listening on http://{SERVER_CONFIG.host}:{SERVER_CONFIG.port}")
    print(f"Local output mode: {'enabled' if SERVER_CONFIG.enable_local_output else 'disabled'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
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
