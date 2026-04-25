from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://www.eduplus.net"
DEFAULT_OUTPUT_DIR = "downloads"
DEFAULT_TIMEOUT = 30
HM_LVT_COOKIE_NAME = "Hm_lvt_bc32be924d31063c4e643e095e69926a"


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


def load_config(
    *,
    config_file: str | Path = "config.json",
    config_json: str | None = None,
    session: str | None = None,
    course_id: str | None = None,
    hm_lvt: str | None = None,
    course_name: str | None = None,
    base_url: str | None = None,
    output: str | None = None,
    timeout: int | None = None,
    require_config_file: bool = False,
) -> EduplusConfig:
    file_config, resolved_config_path = load_json_config_file(config_file)
    if require_config_file and (not resolved_config_path or not resolved_config_path.exists()):
        raise SystemExit(f"Config file not found: {config_file}")

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
            json_config.get("session"),
            json_cookies.get("SESSION"),
            cookies.get("SESSION"),
        )
        or "",
        course_id=first_value(
            course_id,
            json_config.get("course_id"),
            json_config.get("courseId"),
        )
        or "",
        hm_lvt=first_value(
            hm_lvt,
            json_config.get("hm_lvt"),
            json_cookies.get(HM_LVT_COOKIE_NAME),
            cookies.get(HM_LVT_COOKIE_NAME),
        )
        or "",
        course_name=first_value(
            course_name,
            json_config.get("course_name"),
            json_config.get("courseName"),
        )
        or "",
        base_url=first_value(base_url, json_config.get("base_url"), json_config.get("baseUrl"), DEFAULT_BASE_URL)
        or DEFAULT_BASE_URL,
        output=first_value(output, json_config.get("output"), json_config.get("output_dir"), json_config.get("outputDir"), DEFAULT_OUTPUT_DIR)
        or DEFAULT_OUTPUT_DIR,
        timeout=int(first_value(timeout, json_config.get("timeout"), DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT),
        config_path=resolved_config_path if resolved_config_path and resolved_config_path.exists() else None,
    )

    missing = [name for name in ("session", "course_id") if not getattr(config, name)]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing required config: {joined}. Fill config.json or pass command-line overrides.")
    return config


def inspect_config_defaults(config_file: str | Path = "config.json") -> dict[str, Any]:
    try:
        file_config, resolved_config_path = load_json_config_file(config_file)
    except SystemExit:
        return {
            "config_path": "",
            "has_session": False,
            "has_course_id": False,
        }

    cookies = file_config.get("cookies", {})
    if not isinstance(cookies, dict):
        cookies = {}

    has_session = bool(
        first_value(
            file_config.get("session"),
            cookies.get("SESSION"),
        )
    )
    has_course_id = bool(
        first_value(
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
