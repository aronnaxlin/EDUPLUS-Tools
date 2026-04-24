from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.client import EduplusClient
from ..core.config import load_config, mask_value
from ..features.homework import scrape_homework
from ..features.ppt import download_ppt_files


@dataclass
class Job:
    id: str
    command: str
    execution_mode: str = "public"
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    logs: list[str] = field(default_factory=list)
    summary: dict[str, str] = field(default_factory=dict)
    output_root: str = ""
    cleaned_at: float | None = None


DEFAULT_WEB_OUTPUT_ROOT = Path("downloads") / "web-jobs"
DEFAULT_WEB_BUNDLE_ROOT = Path("downloads") / "web-bundles"
DEFAULT_LOCAL_OUTPUT_ROOT = Path("downloads")


@dataclass
class StorageConfig:
    public_output_root: Path = DEFAULT_WEB_OUTPUT_ROOT
    bundle_root: Path = DEFAULT_WEB_BUNDLE_ROOT
    local_output_root: Path = DEFAULT_LOCAL_OUTPUT_ROOT


STORAGE_CONFIG = StorageConfig()


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, command: str, execution_mode: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], command=command, execution_mode=execution_mode)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.logs.append(message)

    def update(self, job_id: str, **changes: object) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in changes.items():
                setattr(job, key, value)

    def all(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())


def configure_storage(*, public_output_root: Path, bundle_root: Path, local_output_root: Path) -> None:
    STORAGE_CONFIG.public_output_root = public_output_root
    STORAGE_CONFIG.bundle_root = bundle_root
    STORAGE_CONFIG.local_output_root = local_output_root


def run_job_async(job_store: JobStore, payload: dict[str, object]) -> Job:
    command = str(payload.get("command") or "all")
    execution_mode = normalize_execution_mode(payload.get("execution_mode"))
    job = job_store.create(command, execution_mode)
    thread = threading.Thread(target=_run_job, args=(job_store, job.id, payload), daemon=True)
    thread.start()
    return job


def serialize_job(job: Job) -> dict[str, object]:
    artifacts = list_job_artifacts(job)
    return {
        "id": job.id,
        "command": job.command,
        "execution_mode": job.execution_mode,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "exit_code": job.exit_code,
        "logs": job.logs,
        "summary": job.summary,
        "output_root": job.output_root,
        "cleaned_at": job.cleaned_at,
        "artifact_count": artifacts["artifact_count"],
        "bundle_url": artifacts["bundle_url"],
    }


def list_job_artifacts(job: Job) -> dict[str, object]:
    if job.cleaned_at is not None:
        return {
            "output_root": job.output_root,
            "artifact_count": 0,
            "total_bytes": 0,
            "bundle_url": None,
            "files": [],
        }

    output_root = Path(job.output_root) if job.output_root else None
    files: list[dict[str, object]] = []
    total_bytes = 0
    if output_root and output_root.exists():
        for file_path in sorted(path for path in output_root.rglob("*") if path.is_file()):
            relative = file_path.relative_to(output_root).as_posix()
            size = file_path.stat().st_size
            total_bytes += size
            files.append({"path": relative, "size": size})

    bundle_url = f"/api/jobs/{job.id}/bundle.zip" if files else None
    return {
        "output_root": str(output_root) if output_root else "",
        "artifact_count": len(files),
        "total_bytes": total_bytes,
        "bundle_url": bundle_url,
        "files": files,
    }


def build_job_bundle(job: Job) -> Path | None:
    if job.cleaned_at is not None:
        return None
    output_root = Path(job.output_root) if job.output_root else None
    if not output_root or not output_root.exists():
        return None
    if not any(path.is_file() for path in output_root.rglob("*")):
        return None

    bundle_root = STORAGE_CONFIG.bundle_root
    bundle_root.mkdir(parents=True, exist_ok=True)
    bundle_base = bundle_root / f"eduplus-job-{job.id}"
    bundle_path = bundle_base.with_suffix(".zip")
    if bundle_path.exists():
        bundle_path.unlink()
    archive_path = shutil.make_archive(str(bundle_base), "zip", root_dir=output_root, base_dir=".")
    return Path(archive_path)


def cleanup_job_artifacts(job: Job, *, delete_output_root: bool) -> None:
    if delete_output_root and job.output_root:
        output_root = Path(job.output_root)
        public_root = STORAGE_CONFIG.public_output_root.resolve()
        try:
            resolved_output = output_root.resolve()
        except FileNotFoundError:
            resolved_output = output_root
        if resolved_output == public_root or public_root in resolved_output.parents:
            shutil.rmtree(output_root, ignore_errors=True)

    cleanup_job_bundle(job)
    job.cleaned_at = time.time()


def cleanup_job_bundle(job: Job) -> None:
    bundle_path = STORAGE_CONFIG.bundle_root / f"eduplus-job-{job.id}.zip"
    if bundle_path.exists():
        bundle_path.unlink()


def _run_job(job_store: JobStore, job_id: str, payload: dict[str, object]) -> None:
    def log(message: str) -> None:
        job_store.append_log(job_id, str(message))

    job_store.update(job_id, status="running", started_at=time.time())
    try:
        execution_mode = normalize_execution_mode(payload.get("execution_mode"))
        output_root = _job_output_root(payload, job_id)
        config = load_config(
            config_file=None,
            session=_string(payload.get("session")),
            course_id=_string(payload.get("course_id")),
            hm_lvt=_string(payload.get("hm_lvt")),
            course_name=_string(payload.get("course_name")),
            base_url=_string(payload.get("base_url")) or None,
            output=str(output_root),
            timeout=_int(payload.get("timeout")),
        )
        client = EduplusClient(config, verbose=bool(payload.get("verbose")), log=log)
        output_root = Path(config.output)

        job_store.update(
            job_id,
            execution_mode=execution_mode,
            output_root=str(output_root),
            summary={
                "mode": execution_mode_label(execution_mode),
                "course_id": config.course_id,
                "course_name": config.course_name,
                "output": str(output_root),
                "session": mask_value(config.session),
            },
        )
        log(f"Course ID: {config.course_id}")
        log(f"SESSION: {mask_value(config.session)}")
        log(f"Output: {output_root}")

        status = 0
        command = str(payload.get("command") or "all")
        if command in {"all", "ppt"}:
            status = max(
                status,
                download_ppt_files(
                    client,
                    course_id=config.course_id,
                    course_name=config.course_name,
                    output_root=output_root,
                    dry_run=bool(payload.get("dry_run")),
                    overwrite=bool(payload.get("overwrite")),
                    log=log,
                ),
            )

        if command in {"all", "homework"} and not bool(payload.get("dry_run")):
            status = max(
                status,
                scrape_homework(
                    client,
                    course_id=config.course_id,
                    output_root=output_root,
                    convert_existing=not bool(payload.get("skip_existing_homework_convert")),
                    log=log,
                ),
            )
        elif command == "all" and bool(payload.get("dry_run")):
            log("Skipping homework because --dry-run only applies to ppt downloads.")

        artifacts = list_job_artifacts(
            job_store.get(job_id) or Job(id=job_id, command=command, execution_mode=execution_mode, output_root=str(output_root))
        )
        summary = {
            "mode": execution_mode_label(execution_mode),
            **config_summary(config, output_root),
            "artifacts": str(artifacts["artifact_count"]),
            "bundle": "ready" if artifacts["artifact_count"] else "empty",
        }
        job_store.update(job_id, status="completed", exit_code=status, finished_at=time.time())
        job_store.update(job_id, summary=summary)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        message = str(exc.code) if exc.code not in (None, 0) else "Job stopped."
        log(message)
        job_store.update(job_id, status="failed", exit_code=code, finished_at=time.time())
    except Exception as exc:
        log(f"Unhandled error: {exc}")
        job_store.update(job_id, status="failed", exit_code=1, finished_at=time.time())


def _string(value: object) -> str:
    return str(value or "").strip()


def _int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _job_output_root(payload: dict[str, object], job_id: str) -> Path:
    execution_mode = normalize_execution_mode(payload.get("execution_mode"))
    output_value = _string(payload.get("output"))
    if execution_mode == "local":
        return Path(output_value or str(STORAGE_CONFIG.local_output_root))
    base_output = Path(output_value or str(STORAGE_CONFIG.public_output_root))
    return base_output / job_id


def config_summary(config: object, output_root: Path) -> dict[str, str]:
    return {
        "course_id": getattr(config, "course_id", ""),
        "course_name": getattr(config, "course_name", ""),
        "output": str(output_root),
        "session": mask_value(getattr(config, "session", "")),
    }


def normalize_execution_mode(value: object) -> str:
    return "local" if str(value or "").strip().lower() == "local" else "public"


def execution_mode_label(mode: str) -> str:
    return "Local output" if mode == "local" else "Public service"
