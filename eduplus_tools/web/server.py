from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .jobs import JobStore, build_job_bundle, list_job_artifacts, run_job_async, serialize_job


WEB_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = WEB_ROOT / "static"
JOB_STORE = JobStore()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EDUPLUS Tools Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="Bind port. Default: 8000")
    return parser


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
                self._serve_file(bundle_path, download_name=f"eduplus-job-{job.id}.zip")
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

        job = run_job_async(JOB_STORE, payload)
        self._send_json({"job_id": job.id}, status=HTTPStatus.ACCEPTED)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _serve_file(self, path: Path, download_name: str | None = None) -> None:
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
    server = ThreadingHTTPServer((args.host, args.port), WebHandler)
    print(f"EDUPLUS Web UI listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
