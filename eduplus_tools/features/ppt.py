from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.client import EduplusClient, course_referer
from ..core.config import safe_filename


@dataclass(frozen=True)
class Courseware:
    node_id: str
    attachment_id: str
    name: str
    suffix: str
    chapter: str
    size_mb: float | None
    signed_url: str | None = None


DEFAULT_FILE_TYPES: tuple[str, ...] = ("ppt", "pptx")
ALL_FILE_TYPES = "*"


def normalize_file_types(file_types: str | list[str] | tuple[str, ...] | None) -> frozenset[str] | None:
    """Turn user input like "pdf,pptx" or ["PDF"] into a suffix set; None means every type."""
    if file_types is None:
        return frozenset(DEFAULT_FILE_TYPES)
    raw = file_types.split(",") if isinstance(file_types, str) else list(file_types)
    cleaned = {str(item).strip().lower().lstrip(".") for item in raw}
    cleaned.discard("")
    if not cleaned:
        return frozenset(DEFAULT_FILE_TYPES)
    if ALL_FILE_TYPES in cleaned or "all" in cleaned:
        return None
    return frozenset(cleaned)


def describe_file_types(types: frozenset[str] | None) -> str:
    return "全部类型" if types is None else "/".join(sorted(t.upper() for t in types))


def collect_courseware(
    client: EduplusClient,
    course_id: str,
    file_types: frozenset[str] | None = frozenset(DEFAULT_FILE_TYPES),
) -> list[Courseware]:
    return collect_courseware_detailed(client, course_id, file_types)[0]


def collect_courseware_detailed(
    client: EduplusClient,
    course_id: str,
    file_types: frozenset[str] | None = frozenset(DEFAULT_FILE_TYPES),
) -> tuple[list[Courseware], dict[str, int]]:
    """Return matching files plus a count of the other file types found in the chapter tree."""
    referer = course_referer(client.base_url, course_id)
    path = f"/api/course/chapters/tree_list?courseId={urllib.parse.quote(course_id)}"
    tree = client.api_json(path, referer)
    if not tree.get("success"):
        raise RuntimeError(f"获取章节树失败：{tree.get('message') or tree}")

    files: list[Courseware] = []
    skipped: dict[str, int] = {}
    walk_chapters(tree.get("data") or [], chapter="", output=files, file_types=file_types, skipped=skipped)
    deduped: dict[str, Courseware] = {}
    for file in files:
        deduped[file.attachment_id] = file
    return list(deduped.values()), dict(sorted(skipped.items(), key=lambda kv: (-kv[1], kv[0])))


def filter_courseware(files: list[Courseware], keyword: str | None = None) -> list[Courseware]:
    cleaned = (keyword or "").strip().lower()
    if not cleaned:
        return list(files)
    return [
        file
        for file in files
        if cleaned in file.name.lower() or cleaned in file.chapter.lower() or cleaned == file.attachment_id.lower()
    ]


def get_course_name(client: EduplusClient, course_id: str, log: Callable[[str], None] = print) -> str | None:
    referer = course_referer(client.base_url, course_id)
    path = f"/api/course/courses/v1/{urllib.parse.quote(course_id)}"
    try:
        payload = client.api_json(path, referer)
    except (urllib.error.URLError, RuntimeError, OSError) as exc:
        if client.verbose:
            log(f"获取课程名称失败：{exc}")
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    for key in ("name", "courseName", "title", "courseTitle"):
        value = data.get(key)
        if value:
            return str(value).strip()
    return None


def walk_chapters(
    nodes: list[dict[str, Any]],
    chapter: str,
    output: list[Courseware],
    file_types: frozenset[str] | None = frozenset(DEFAULT_FILE_TYPES),
    skipped: dict[str, int] | None = None,
) -> None:
    for node in nodes:
        node_name = str(node.get("name") or "")
        node_type = str(node.get("type") or "")
        current_chapter = node_name if node_type.lower() == "chapter" else chapter

        suffix = node_suffix(node)
        attachment_id = str(node.get("resourceId") or "")
        if node_type.lower() != "chapter" and (attachment_id or suffix):
            if attachment_id and suffix and (file_types is None or suffix in file_types):
                output.append(
                    Courseware(
                        node_id=str(node.get("id") or ""),
                        attachment_id=attachment_id,
                        name=node_name or f"{attachment_id}.{suffix}",
                        suffix=suffix,
                        chapter=chapter,
                        size_mb=float(node["fileSize"]) if isinstance(node.get("fileSize"), (int, float)) else None,
                    )
                )
            elif skipped is not None:
                key = suffix or f"type:{node_type or 'unknown'}"
                skipped[key] = skipped.get(key, 0) + 1

        children = node.get("children") or []
        if isinstance(children, list):
            walk_chapters(children, current_chapter, output, file_types, skipped)


def node_suffix(node: dict[str, Any]) -> str:
    """Best-effort file extension of a chapter-tree node: fileSuffix, then the name, then a ppt node type."""
    suffix = str(node.get("fileSuffix") or "").strip().lower().lstrip(".")
    if suffix:
        return suffix
    match = re.search(r"\.([A-Za-z0-9]{1,5})$", str(node.get("name") or ""))
    if match:
        return match.group(1).lower()
    return "ppt" if str(node.get("type") or "").lower() == "ppt" else ""


def is_ppt_node(node: dict[str, Any]) -> bool:
    return node_suffix(node) in DEFAULT_FILE_TYPES


def suffix_from_name(name: str) -> str:
    match = re.search(r"\.([A-Za-z0-9]{1,5})$", name)
    return match.group(1).lower() if match else "ppt"


def attach_signed_urls(
    client: EduplusClient,
    course_id: str,
    files: list[Courseware],
    log: Callable[[str], None] = print,
) -> list[Courseware]:
    referer = course_referer(client.base_url, course_id)
    enriched: list[Courseware] = []
    for file in files:
        path = f"/api/attachment/attachments/{urllib.parse.quote(file.attachment_id)}/viewUrl"
        payload = client.api_json(path, referer)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        signed_url = data.get("url")
        origin_name = data.get("originFileName")
        if not payload.get("success") or not signed_url:
            log(f"跳过 {file.name}：{payload.get('message') or '缺少签名下载链接'}")
            continue
        enriched.append(
            Courseware(
                node_id=file.node_id,
                attachment_id=file.attachment_id,
                name=str(origin_name or file.name),
                suffix=str(data.get("type") or file.suffix or suffix_from_name(file.name)).lower(),
                chapter=file.chapter,
                size_mb=file.size_mb,
                signed_url=str(signed_url),
            )
        )
        time.sleep(0.05)
    return enriched


def inspect_courseware(
    client: EduplusClient,
    *,
    course_id: str,
    course_name: str | None = None,
    keyword: str | None = None,
    include_urls: bool = True,
    file_types: str | list[str] | tuple[str, ...] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """List courseware for a course, optionally filtering by keyword and attaching signed URLs."""
    types = normalize_file_types(file_types)
    detected_course_name = course_name or get_course_name(client, course_id, log=log) or ""
    all_files, other_types = collect_courseware_detailed(client, course_id, types)
    selected = filter_courseware(all_files, keyword)
    if include_urls and selected:
        selected = attach_signed_urls(client, course_id, selected, log=log)
    if not all_files:
        log(not_found_message(types, other_types))
    return {
        "course_id": course_id,
        "course_name": detected_course_name,
        "file_types": "*" if types is None else sorted(types),
        "total_count": len(all_files),
        "matched_count": len(selected),
        "other_file_types": other_types,
        "items": [asdict(item) for item in selected],
    }


def not_found_message(types: frozenset[str] | None, other_types: dict[str, int]) -> str:
    message = f"未找到 {describe_file_types(types)} 课件。"
    if other_types:
        listed = "、".join(f"{key}×{count}" for key, count in other_types.items())
        message += f"章节树里有其他类型的资源：{listed}；可用 file_types 指定（如 pdf，或 * 表示全部）。"
    else:
        message += "章节树里没有任何附件资源。"
    return message


def course_output_dir(base_output_dir: Path, course_id: str, course_name: str | None) -> Path:
    ppt_root = base_output_dir / "courseware"
    if course_name:
        return ppt_root / f"{safe_filename(course_name, 'course')}__{course_id}"
    return ppt_root / course_id


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"重名文件过多：{path}")


def looks_like_presentation(data: bytes, filename: str) -> bool:
    if re.search(r"\.pptx?$", filename, re.I):
        return True
    return data.startswith(b"PK\x03\x04") or data.startswith(b"\xd0\xcf\x11\xe0")


def looks_like_valid_download(data: bytes, filename: str) -> bool:
    """Reject HTML/JSON error pages that a CDN sometimes returns instead of the file."""
    if not data:
        return False
    if filename.lower().endswith(".pdf"):
        return data.startswith(b"%PDF")
    head = data[:512].lstrip().lower()
    return not (head.startswith(b"<!doctype html") or head.startswith(b"<html") or head.startswith(b'{"'))


def download_ppt_detailed(
    client: EduplusClient,
    *,
    course_id: str,
    course_name: str | None,
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = False,
    keyword: str | None = None,
    file_types: str | list[str] | tuple[str, ...] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Inspect and optionally download courseware (PPT/PPTX by default), returning structured results."""
    types = normalize_file_types(file_types)
    label = describe_file_types(types)
    detected_course_name = course_name or get_course_name(client, course_id, log=log)
    output_dir = course_output_dir(output_root, course_id, detected_course_name)
    all_files, other_types = collect_courseware_detailed(client, course_id, types)
    files = filter_courseware(all_files, keyword)
    if not files:
        log(f"未找到匹配的 {label} 课件。" if keyword and all_files else not_found_message(types, other_types))
        return {
            "exit_code": 0,
            "course_id": course_id,
            "course_name": detected_course_name or "",
            "output_dir": str(output_dir),
            "dry_run": dry_run,
            "other_file_types": other_types,
            "items": [],
            "downloaded_files": [],
            "failed": [],
        }

    files = attach_signed_urls(client, course_id, files, log=log)
    log(f"找到 {len(files)} 个 {label} 文件。")
    if detected_course_name:
        log(f"课程名称：{detected_course_name}")
    log(f"输出目录：{output_dir}")

    items = [asdict(file) for file in files]
    if dry_run:
        for file in files:
            size = f" ({file.size_mb} MB)" if file.size_mb is not None else ""
            log(f"{file.name}{size}")
            log(f"  附件 ID：{file.attachment_id}")
            log(f"  下载链接：{file.signed_url}")
        return {
            "exit_code": 0,
            "course_id": course_id,
            "course_name": detected_course_name or "",
            "output_dir": str(output_dir),
            "dry_run": True,
            "items": items,
            "downloaded_files": [],
            "failed": [],
        }

    referer = course_referer(client.base_url, course_id)
    downloaded_files: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for index, file in enumerate(files, start=1):
        suffix = file.suffix or "ppt"
        filename = safe_filename(file.name, f"courseware.{suffix}")
        if not filename.lower().endswith(f".{suffix}"):
            filename = f"{filename}.{suffix}"
        destination = output_dir / filename
        try:
            if file.signed_url is None:
                raise RuntimeError("缺少签名下载链接")
            data = client.download_bytes(file.signed_url, referer)
            final_path = destination if overwrite else unique_path(destination)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if suffix in DEFAULT_FILE_TYPES:
                if not looks_like_presentation(data, final_path.name):
                    raise RuntimeError("返回内容不是有效的 PPT/PPTX 文件")
            elif not looks_like_valid_download(data, final_path.name):
                raise RuntimeError(f"返回内容不是有效的 {suffix.upper()} 文件")
            final_path.write_bytes(data)
            downloaded_files.append(
                {
                    "attachment_id": file.attachment_id,
                    "name": file.name,
                    "chapter": file.chapter,
                    "path": str(final_path),
                    "size_bytes": len(data),
                }
            )
            log(f"[{index}/{len(files)}] 已下载 {final_path}（{len(data)} 字节）")
        except (urllib.error.URLError, RuntimeError, OSError) as exc:
            failed.append({"attachment_id": file.attachment_id, "name": file.name, "error": str(exc)})
            log(f"[{index}/{len(files)}] 下载失败 {file.name}：{exc}")

    log(f"完成。成功 {len(downloaded_files)} 个，失败 {len(failed)} 个。")
    return {
        "exit_code": 1 if failed else 0,
        "course_id": course_id,
        "course_name": detected_course_name or "",
        "output_dir": str(output_dir),
        "dry_run": False,
        "items": items,
        "downloaded_files": downloaded_files,
        "failed": failed,
    }


def download_ppt_files(
    client: EduplusClient,
    *,
    course_id: str,
    course_name: str | None,
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = False,
    keyword: str | None = None,
    file_types: str | list[str] | tuple[str, ...] | None = None,
    log: Callable[[str], None] = print,
) -> int:
    result = download_ppt_detailed(
        client,
        course_id=course_id,
        course_name=course_name,
        output_root=output_root,
        dry_run=dry_run,
        overwrite=overwrite,
        keyword=keyword,
        file_types=file_types,
        log=log,
    )
    return int(result["exit_code"])
