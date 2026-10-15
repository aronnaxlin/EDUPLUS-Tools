from __future__ import annotations

from datetime import datetime

from eduplus_tools.features.status import (
    CHINA_TZ,
    ms_to_iso,
    normalize_course,
    normalize_experiment,
    normalize_homework,
    task_to_api,
)

from status_fixtures import BASE_URL, course, experiment_item, homework_item, ms


def test_ms_to_iso_uses_china_time() -> None:
    assert ms_to_iso(1793462399000) == "2026-10-31T23:59:59+08:00"
    assert ms_to_iso(None) is None


def test_normalize_course() -> None:
    result = normalize_course({"id": "c1", "name": "操作系统", "term": "2026-2027第一学期", "startTime": ms("2026-09-01T00:00:00"), "endTime": ms("2027-01-31T23:59:59")})
    assert result is not None
    assert result.end_at == "2027-01-31T23:59:59+08:00"


def test_normalize_homework_state_follows_sub_status_not_submit_task() -> None:
    submitted = normalize_homework(homework_item(sub_status=1, submit_task=0), course(), BASE_URL)
    saved_only = normalize_homework(homework_item(sub_status=0, submit_task=1), course(), BASE_URL)
    graded = normalize_homework(homework_item(sub_status=3, submit_task=1), course(), BASE_URL)
    assert submitted is not None and submitted.state == "submitted"
    assert saved_only is not None and saved_only.state == "not_submitted"
    assert graded is not None and graded.state == "graded"


def test_normalize_homework_fields() -> None:
    result = normalize_homework(homework_item(publish_id="pub9"), course("c9"), BASE_URL)
    assert result is not None
    assert result.id == "homework:pub9"
    assert result.due_at == "2026-10-15T23:59:59+08:00"
    assert result.published_at == "2026-10-09T10:20:59+08:00"
    assert result.late_submission_allowed is True
    assert result.url == "https://www.eduplus.net/course/course/c9/HomeworkPage?userRole="


def test_normalize_experiment_report_means_submitted() -> None:
    pending = normalize_experiment(experiment_item(), course(), BASE_URL)
    done = normalize_experiment(experiment_item(reports=[{"status": "Submitted"}]), course(), BASE_URL)
    assert pending is not None and pending.state == "not_submitted"
    assert done is not None and done.state == "submitted"
    assert done.deliverables == ["report"]
    assert done.published_at is None
    assert done.upstream["report_statuses"] == ["Submitted"]


def test_task_to_api_overdue_only_when_not_submitted() -> None:
    now = datetime(2026, 10, 16, tzinfo=CHINA_TZ)
    late = normalize_homework(homework_item(sub_status=0), course(), BASE_URL)
    handed_in = normalize_homework(homework_item(sub_status=1), course(), BASE_URL)
    assert late is not None and handed_in is not None
    assert task_to_api(late, now)["overdue"] is True
    assert task_to_api(handed_in, now)["overdue"] is False
    assert task_to_api(late, now)["course"] == {"id": "c1", "name": "课程c1"}
