from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://www.eduplus.net"
DEFAULT_OUTPUT_DIR = "downloads"
DEFAULT_TIMEOUT = 30
HM_LVT_COOKIE_NAME = "Hm_lvt_bc32be924d31063c4e643e095e69926a"

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_OVERRIDES: dict[str, str] = {}


@dataclass(frozen=True)
class EduplusConfig:
    session: str
    course_id: str
    hm_lvt: str = ""
    course_name: str = ""
    base_url: str = DEFAULT_BASE_URL
    output: str = DEFAULT_OUTPUT_DIR
    timeout: int = DEFAULT_TIMEOUT
    config_path: Path | None = None


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def set_runtime_overrides(**values: str | None) -> None:
    """Update in-process credential/config overrides (used when config.json is read-only)."""
    with _RUNTIME_LOCK:
        for key, value in values.items():
            if value is None:
                continue
            cleaned = str(value).strip()
            if cleaned:
                _RUNTIME_OVERRIDES[key] = cleaned
            else:
                _RUNTIME_OVERRIDES.pop(key, None)


def clear_runtime_overrides() -> None:
    with _RUNTIME_LOCK:
        _RUNTIME_OVERRIDES.clear()


def get_runtime_override(key: str) -> str | None:
    with _RUNTIME_LOCK:
        return _RUNTIME_OVERRIDES.get(key)


def load_json_config_file(path: str | Path | None) -> tuple[dict[str, Any], Path | None]:
    if not path:
        return {}, None

    config_path = resolve_config_path(Path(path))
    if not config_path.exists():
        return {}, config_path

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON config file {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"JSON config file {config_path} must contain an object")
    return data, config_path


def resolve_config_path(path: Path) -> Path:
    if path.is_absolute():
        return path

    candidates = [
        path,
        Path.cwd() / path,
        project_root() / path,
        package_root() / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return path


CREDENTIALS_FILENAME = "credentials.json"
PERSISTED_KEYS = ("session", "course_id")


def data_dir() -> Path:
    """Directory for server state that must survive restarts (EDUPLUS_DATA_DIR, default ./data)."""
    return Path(_env_value("EDUPLUS_DATA_DIR") or "data")


def credentials_path() -> Path:
    return data_dir() / CREDENTIALS_FILENAME


def _fingerprint(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def write_private_file(path: Path, content: str) -> None:
    """Atomically write a file readable only by the current user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(tmp, path)


def save_persisted_credentials(*, session: str | None = None, course_id: str | None = None) -> tuple[Path, bool, str | None]:
    """Persist credentials updated at runtime (e.g. via MCP) so they survive restarts.

    The file records a fingerprint of EDUPLUS_SESSION at save time. If the operator later
    changes EDUPLUS_SESSION (e.g. edits .env), the fingerprint no longer matches and the
    newer env value wins again. Returns (path, persisted_to_disk, error_message).
    """
    target = credentials_path()
    data = _read_persisted_credentials_file(target)
    if session:
        data["session"] = session
    if course_id is not None:
        if course_id:
            data["course_id"] = course_id
        else:
            data.pop("course_id", None)
    data["env_session_sha256"] = _fingerprint(_env_value("EDUPLUS_SESSION"))
    try:
        write_private_file(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return target, True, None
    except OSError as exc:
        return target, False, str(exc)


def _read_persisted_credentials_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def get_persisted_credential(key: str) -> str | None:
    if key not in PERSISTED_KEYS:
        return None
    data = _read_persisted_credentials_file(credentials_path())
    if data.get("env_session_sha256") != _fingerprint(_env_value("EDUPLUS_SESSION")):
        return None
    value = data.get(key)
    return str(value).strip() or None if value is not None else None


def parse_json_config(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON config: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("JSON config must be an object")
    return data


def first_value(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text != "":
            return text
    return None


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def load_config(
    *,
    config_file: str | Path | None = "config.json",
    config_json: str | None = None,
    session: str | None = None,
    course_id: str | None = None,
    hm_lvt: str | None = None,
    course_name: str | None = None,
    base_url: str | None = None,
    output: str | None = None,
    timeout: int | None = None,
    require_config_file: bool = False,
    require_course_id: bool = True,
    use_server_defaults: bool = True,
) -> EduplusConfig:
    """Resolve config from explicit values and, unless disabled, server-side defaults.

    Precedence: explicit args > runtime overrides > config_json > persisted
    credentials (data/credentials.json) > EDUPLUS_* env vars > config file > defaults.

    use_server_defaults=False ignores config.json, persisted credentials, EDUPLUS_* env
    vars, and runtime overrides, so only explicit arguments and config_json apply. Public web jobs use
    this so visitors can never borrow the server owner's session.
    """
    if use_server_defaults:
        runtime_value: Callable[[str], str | None] = get_runtime_override
        env_value: Callable[[str], str | None] = _env_value
        persisted_value: Callable[[str], str | None] = get_persisted_credential
    else:
        runtime_value = env_value = persisted_value = lambda _name: None
        config_file = None

    effective_config_file = config_file
    if config_file is not None and str(config_file) == "config.json":
        env_config = _env_value("EDUPLUS_CONFIG")
        if env_config:
            effective_config_file = env_config

    file_config, resolved_config_path = load_json_config_file(effective_config_file)
    if require_config_file and (not resolved_config_path or not resolved_config_path.exists()):
        raise SystemExit(f"Config file not found: {effective_config_file}")

    cookies = file_config.get("cookies", {})
    if not isinstance(cookies, dict):
        cookies = {}

    arg_json_config = parse_json_config(config_json)
    json_config = {**file_config, **arg_json_config}
    json_cookies = json_config.get("cookies", {})
    if not isinstance(json_cookies, dict):
        json_cookies = {}

    config = EduplusConfig(
        session=first_value(
            session,
            runtime_value("session"),
            arg_json_config.get("session"),
            persisted_value("session"),
            env_value("EDUPLUS_SESSION"),
            json_config.get("session"),
            json_cookies.get("SESSION"),
            cookies.get("SESSION"),
        )
        or "",
        course_id=first_value(
            course_id,
            runtime_value("course_id"),
            arg_json_config.get("course_id"),
            arg_json_config.get("courseId"),
            persisted_value("course_id"),
            env_value("EDUPLUS_COURSE_ID"),
            json_config.get("course_id"),
            json_config.get("courseId"),
        )
        or "",
        hm_lvt=first_value(
            hm_lvt,
            runtime_value("hm_lvt"),
            arg_json_config.get("hm_lvt"),
            env_value("EDUPLUS_HM_LVT"),
            json_config.get("hm_lvt"),
            json_cookies.get(HM_LVT_COOKIE_NAME),
            cookies.get(HM_LVT_COOKIE_NAME),
        )
        or "",
        course_name=first_value(
            course_name,
            runtime_value("course_name"),
            arg_json_config.get("course_name"),
            arg_json_config.get("courseName"),
            env_value("EDUPLUS_COURSE_NAME"),
            json_config.get("course_name"),
            json_config.get("courseName"),
        )
        or "",
        base_url=first_value(
            base_url,
            runtime_value("base_url"),
            arg_json_config.get("base_url"),
            arg_json_config.get("baseUrl"),
            env_value("EDUPLUS_BASE_URL"),
            json_config.get("base_url"),
            json_config.get("baseUrl"),
            DEFAULT_BASE_URL,
        )
        or DEFAULT_BASE_URL,
        output=first_value(
            output,
            runtime_value("output"),
            arg_json_config.get("output"),
            arg_json_config.get("output_dir"),
            arg_json_config.get("outputDir"),
            env_value("EDUPLUS_OUTPUT"),
            json_config.get("output"),
            json_config.get("output_dir"),
            json_config.get("outputDir"),
            DEFAULT_OUTPUT_DIR,
        )
        or DEFAULT_OUTPUT_DIR,
        timeout=int(
            first_value(
                timeout,
                runtime_value("timeout"),
                arg_json_config.get("timeout"),
                env_value("EDUPLUS_TIMEOUT"),
                json_config.get("timeout"),
                DEFAULT_TIMEOUT,
            )
            or DEFAULT_TIMEOUT
        ),
        config_path=resolved_config_path if resolved_config_path and resolved_config_path.exists() else None,
    )

    required = ("session", "course_id") if require_course_id else ("session",)
    missing = [name for name in required if not getattr(config, name)]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing required config: {joined}. Fill config.json, set EDUPLUS_* env vars, or pass overrides.")
    return config


def inspect_config_defaults(config_file: str | Path = "config.json") -> dict[str, Any]:
    effective_config_file = config_file
    if str(config_file) == "config.json":
        env_config = _env_value("EDUPLUS_CONFIG")
        if env_config:
            effective_config_file = env_config

    try:
        file_config, resolved_config_path = load_json_config_file(effective_config_file)
    except SystemExit:
        return {
            "config_path": "",
            "has_session": bool(get_runtime_override("session") or get_persisted_credential("session") or _env_value("EDUPLUS_SESSION")),
            "has_course_id": bool(get_runtime_override("course_id") or get_persisted_credential("course_id") or _env_value("EDUPLUS_COURSE_ID")),
        }

    cookies = file_config.get("cookies", {})
    if not isinstance(cookies, dict):
        cookies = {}

    has_session = bool(
        first_value(
            get_runtime_override("session"),
            get_persisted_credential("session"),
            _env_value("EDUPLUS_SESSION"),
            file_config.get("session"),
            cookies.get("SESSION"),
        )
    )
    has_course_id = bool(
        first_value(
            get_runtime_override("course_id"),
            get_persisted_credential("course_id"),
            _env_value("EDUPLUS_COURSE_ID"),
            file_config.get("course_id"),
            file_config.get("courseId"),
        )
    )
    return {
        "config_path": str(resolved_config_path) if resolved_config_path and resolved_config_path.exists() else "",
        "has_session": has_session,
        "has_course_id": has_course_id,
    }


def mask_value(value: str, visible: int = 6) -> str:
    if not value:
        return "(empty)"
    if len(value) <= visible * 2:
        return value
    return f"{value[:visible]}...{value[-visible:]}"


def safe_filename(name: str, fallback: str = "file") -> str:
    import re

    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", str(name)).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:180] or fallback
