from __future__ import annotations

from unittest.mock import Mock

import pytest

from eduplus_tools.features import ppt
from eduplus_tools.features.ppt import (
    download_ppt_detailed,
    inspect_courseware,
    normalize_file_types,
)

TREE = {
    "success": True,
    "data": [
        {
            "id": "ch1",
            "name": "第2章 密码学概论",
            "type": "chapter",
            "children": [
                {"id": "n1", "name": "密码学概论.pdf", "type": "pdf", "resourceId": "att1", "fileSuffix": "pdf", "fileSize": 3.0},
                {"id": "n2", "name": "古典密码.PDF", "type": "file", "resourceId": "att2"},
                {"id": "n3", "name": "讲解视频.mp4", "type": "video", "resourceId": "att3", "fileSuffix": "mp4"},
                {"id": "n4", "name": "随堂测1", "type": "homework"},
            ],
        }
    ],
}


class FakeClient:
    base_url = "https://example.edu"
    verbose = False

    def __init__(self) -> None:
        self.downloads: dict[str, bytes] = {}

    def api_json(self, path: str, referer: str | None = None):
        if path.startswith("/api/course/chapters/tree_list"):
            return TREE
        if path.startswith("/api/course/courses/v1/"):
            return {"data": {"name": "信息安全概论"}}
        if path.startswith("/api/attachment/attachments/"):
            att = path.split("/")[4]
            return {"success": True, "data": {"url": f"https://cdn.example/{att}"}}
        raise AssertionError(path)

    def download_bytes(self, url: str, referer: str | None = None) -> bytes:
        return self.downloads[url]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ppt.time, "sleep", Mock())


def test_normalize_file_types() -> None:
    assert normalize_file_types(None) == {"ppt", "pptx"}
    assert normalize_file_types("PDF, .pptx") == {"pdf", "pptx"}
    assert normalize_file_types(["*"]) is None
    assert normalize_file_types("") == {"ppt", "pptx"}


def test_default_ppt_only_reports_other_file_types() -> None:
    logs: list[str] = []
    result = inspect_courseware(FakeClient(), course_id="c1", log=logs.append)
    assert result["total_count"] == 0
    assert result["other_file_types"] == {"pdf": 2, "mp4": 1}
    assert any("pdf×2" in line and "file_types" in line for line in logs)


def test_pdf_file_types_lists_pdfs_including_suffix_from_name() -> None:
    result = inspect_courseware(FakeClient(), course_id="c1", file_types=["pdf"], include_urls=False)
    assert [item["attachment_id"] for item in result["items"]] == ["att1", "att2"]
    assert {item["suffix"] for item in result["items"]} == {"pdf"}
    assert all(item["chapter"] == "第2章 密码学概论" for item in result["items"])
    assert result["other_file_types"] == {"mp4": 1}


def test_star_lists_every_attachment() -> None:
    result = inspect_courseware(FakeClient(), course_id="c1", file_types="*", include_urls=False)
    assert result["total_count"] == 3
    assert result["file_types"] == "*"


def test_download_pdf_validates_magic_bytes(tmp_path) -> None:
    client = FakeClient()
    client.downloads = {
        "https://cdn.example/att1": b"%PDF-1.7 real",
        "https://cdn.example/att2": b"<html>expired</html>",
    }
    result = download_ppt_detailed(
        client, course_id="c1", course_name=None, output_root=tmp_path, file_types="pdf", log=lambda _: None
    )
    assert [item["name"] for item in result["downloaded_files"]] == ["密码学概论.pdf"]
    assert result["downloaded_files"][0]["path"].endswith(".pdf")
    assert [item["attachment_id"] for item in result["failed"]] == ["att2"]
    assert result["exit_code"] == 1
