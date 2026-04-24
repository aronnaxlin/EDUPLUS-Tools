from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
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


def collect_courseware(client: EduplusClient, course_id: str) -> list[Courseware]:
    referer = course_referer(client.base_url, course_id)
    path = f"/api/course/chapters/tree_list?courseId={urllib.parse.quote(course_id)}"
    tree = client.api_json(path, referer)
    if not tree.get("success"):
        raise RuntimeError(f"Failed to fetch chapter tree: {tree.get('message') or tree}")

    files: list[Courseware] = []
    walk_chapters(tree.get("data") or [], chapter="", output=files)
    deduped: dict[str, Courseware] = {}
    for file in files:
        deduped[file.attachment_id] = file
    return list(deduped.values())


def get_course_name(client: EduplusClient, course_id: str, log: Callable[[str], None] = print) -> str | None:
    referer = course_referer(client.base_url, course_id)
    path = f"/api/course/courses/v1/{urllib.parse.quote(course_id)}"
    try:
        payload = client.api_json(path, referer)
    except (urllib.error.URLError, RuntimeError, OSError) as exc:
        if client.verbose:
            log(f"Could not fetch course name: {exc}")
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    for key in ("name", "courseName", "title", "courseTitle"):
        value = data.get(key)
        if value:
            return str(value).strip()
    return None


def walk_chapters(nodes: list[dict[str, Any]], chapter: str, output: list[Courseware]) -> None:
    for node in nodes:
        node_name = str(node.get("name") or "")
        node_type = str(node.get("type") or "")
        suffix = str(node.get("fileSuffix") or "").lower()
        current_chapter = node_name if node_type.lower() == "chapter" else chapter

        if is_ppt_node(node):
            attachment_id = str(node.get("resourceId") or "")
            if attachment_id:
                output.append(
                    Courseware(
                        node_id=str(node.get("id") or ""),
                        attachment_id=attachment_id,
                        name=node_name or f"{attachment_id}.{suffix or 'ppt'}",
                        suffix=suffix or suffix_from_name(node_name),
                        chapter=chapter,
                        size_mb=float(node["fileSize"]) if isinstance(node.get("fileSize"), (int, float)) else None,
                    )
                )

        children = node.get("children") or []
        if isinstance(children, list):
            walk_chapters(children, current_chapter, output)


def is_ppt_node(node: dict[str, Any]) -> bool:
    node_type = str(node.get("type") or "")
    suffix = str(node.get("fileSuffix") or "")
    name = str(node.get("name") or "")
    return node_type.lower() == "ppt" or suffix.lower() in {"ppt", "pptx"} or bool(re.search(r"\.pptx?$", name, re.I))


def suffix_from_name(name: str) -> str:
    match = re.search(r"\.(pptx?)$", name, re.I)
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
            log(f"skip {file.name}: {payload.get('message') or 'missing signed URL'}")
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
    raise RuntimeError(f"Too many duplicate files for {path}")


def looks_like_presentation(data: bytes, filename: str) -> bool:
    if re.search(r"\.pptx?$", filename, re.I):
        return True
    return data.startswith(b"PK\x03\x04") or data.startswith(b"\xd0\xcf\x11\xe0")


def download_ppt_files(
    client: EduplusClient,
    *,
    course_id: str,
    course_name: str | None,
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = False,
    log: Callable[[str], None] = print,
) -> int:
    detected_course_name = course_name or get_course_name(client, course_id, log=log)
    output_dir = course_output_dir(output_root, course_id, detected_course_name)
    files = collect_courseware(client, course_id)
    if not files:
        log("No PPT/PPTX courseware found.")
        return 0

    files = attach_signed_urls(client, course_id, files, log=log)
    log(f"Found {len(files)} PPT/PPTX file(s).")
    if detected_course_name:
        log(f"Course: {detected_course_name}")
    log(f"Output directory: {output_dir}")

    if dry_run:
        for file in files:
            size = f" ({file.size_mb} MB)" if file.size_mb is not None else ""
            log(f"{file.name}{size}")
            log(f"  attachment_id={file.attachment_id}")
            log(f"  url={file.signed_url}")
        return 0

    referer = course_referer(client.base_url, course_id)
    ok = 0
    failed = 0
    for index, file in enumerate(files, start=1):
        filename = safe_filename(file.name, "courseware.ppt")
        if not re.search(r"\.pptx?$", filename, re.I):
            filename = f"{filename}.{file.suffix or 'ppt'}"
        destination = output_dir / filename
        try:
            if file.signed_url is None:
                raise RuntimeError("missing signed URL")
            data = client.download_bytes(file.signed_url, referer)
            final_path = destination if overwrite else unique_path(destination)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            if not looks_like_presentation(data, final_path.name):
                raise RuntimeError("response does not look like a PPT/PPTX file")
            final_path.write_bytes(data)
            ok += 1
            log(f"[{index}/{len(files)}] downloaded {final_path} ({len(data)} bytes)")
        except (urllib.error.URLError, RuntimeError, OSError) as exc:
            failed += 1
            log(f"[{index}/{len(files)}] failed {file.name}: {exc}")

    log(f"Done. {ok} downloaded, {failed} failed.")
    return 1 if failed else 0
