from __future__ import annotations

import json
import urllib.error
import urllib.parse
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from ..core.client import EduplusClient
from ..core.times import CHINA_TZ, parse_iso
from .homework import is_submitted_sub_status


UPSTREAM_OK = 2000000
# 4010000: not logged in; 4000203: the frontend logs out and asks to log in again.
UPSTREAM_SESSION_EXPIRED_CODES = {4010000, 4000203}

TaskType = Literal["homework", "experiment"]
TaskState = Literal["not_submitted", "submitted", "graded"]
TASK_TYPES: tuple[TaskType, ...] = ("homework", "experiment")
TASK_STATES: tuple[TaskState, ...] = ("not_submitted", "submitted", "graded")


class UpstreamError(RuntimeError):
    def __init__(self, message: str, upstream_code: int | None = None) -> None:
        super().__init__(message)
        self.upstream_code = upstream_code


class SessionExpiredError(UpstreamError):
    pass


@dataclass
class Course:
    id: str
    name: str
    term: str | None = None
    start_at: str | None = None
    end_at: str | None = None


@dataclass
class Task:
    id: str
    type: TaskType
    course_id: str
    course_name: str
    title: str
    state: TaskState
    url: str
    due_at: str | None = None
    published_at: str | None = None
    first_seen_at: str = ""
    late_submission_allowed: bool | None = None
    score: float | None = None
    total_score: float | None = None
    deliverables: list[str] = field(default_factory=list)
    upstream: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanFailure:
    course_id: str
    type: TaskType
    error: str


@dataclass
class SessionCheck:
    valid: bool
    upstream_code: int | None


@dataclass
class FetchResult:
    courses: list[Course]
    tasks: list[Task]
    failures: list[ScanFailure]


def now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def ms_to_iso(value: Any) -> str | None:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=CHINA_TZ).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def course_page_url(base_url: str, course_id: str, page: str) -> str:
    return f"{base_url.rstrip('/')}/course/course/{urllib.parse.quote(course_id)}/{page}?userRole="


def homework_state(sub_status: Any) -> TaskState:
    if str(sub_status) == "3":
        return "graded"
    return "submitted" if is_submitted_sub_status(sub_status) else "not_submitted"


def normalize_course(item: dict[str, Any]) -> Course | None:
    course_id = str(item.get("id") or "")
    if not course_id:
        return None
    term = item.get("term")
    return Course(
        id=course_id,
        name=str(item.get("name") or ""),
        term=str(term) if term else None,
        start_at=ms_to_iso(item.get("startTime")),
        end_at=ms_to_iso(item.get("endTime")),
    )


def normalize_homework(item: dict[str, Any], course: Course, base_url: str) -> Task | None:
    homework = item.get("homeworkDTO")
    publish_id = str(item.get("id") or "")
    if not publish_id or not isinstance(homework, dict):
        return None
    delay_submit = item.get("delaySubmit")
    return Task(
        id=f"homework:{publish_id}",
        type="homework",
        course_id=course.id,
        course_name=course.name,
        title=str(homework.get("name") or ""),
        state=homework_state(homework.get("subStatus")),
        url=course_page_url(base_url, course.id, "HomeworkPage"),
        due_at=ms_to_iso(item.get("endTime")),
        published_at=ms_to_iso(item.get("createTime")),
        late_submission_allowed=None if delay_submit is None else str(delay_submit) == "1",
        score=to_number(homework.get("userScore")),
        total_score=to_number(homework.get("totalScore")),
        upstream={
            "homework_id": homework.get("id"),
            "sub_status": homework.get("subStatus"),
            "submit_task": item.get("submitTask"),
            "status": homework.get("status"),
        },
    )


def normalize_experiment(item: dict[str, Any], course: Course, base_url: str) -> Task | None:
    publish_id = str(item.get("id") or "")
    if not publish_id:
        return None
    experiment = item.get("experiment") if isinstance(item.get("experiment"), dict) else {}
    reports = [report for report in item.get("experimentReports") or [] if isinstance(report, dict)]
    deliverables = [name for name, key in (("report", "reportRequired"), ("code", "codeRequired")) if item.get(key) == "Yes"]
    return Task(
        id=f"experiment:{publish_id}",
        type="experiment",
        course_id=course.id,
        course_name=course.name,
        title=str(experiment.get("name") or ""),
        state="submitted" if reports else "not_submitted",
        url=course_page_url(base_url, course.id, "ExperimentPage"),
        due_at=ms_to_iso(item.get("submitTime")),
        deliverables=deliverables,
        upstream={
            "testcase_id": item.get("testcaseId"),
            "teach_class_id": item.get("teachClassId"),
            "status": item.get("status"),
            "report_statuses": [report.get("status") for report in reports],
        },
    )


def course_is_active(course: Course, now: datetime) -> bool:
    end_at = parse_iso(course.end_at)
    return end_at is None or end_at > now


def task_is_overdue(task: Task, now: datetime) -> bool:
    due_at = parse_iso(task.due_at)
    return task.state == "not_submitted" and due_at is not None and due_at < now


def filter_tasks(
    tasks: Iterable[Task],
    courses: Iterable[Course],
    now: datetime,
    *,
    types: Collection[str] = (),
    states: Collection[str] = (),
    course_ids: Collection[str] = (),
    active_courses_only: bool = True,
    overdue: bool | None = None,
    due_after: datetime | None = None,
    due_before: datetime | None = None,
) -> list[Task]:
    """Shared task query used by the status API and the CLI; sorted by due date, undated last."""
    active_courses = {course.id for course in courses if course_is_active(course, now)}
    selected = []
    for task in tasks:
        due_at = parse_iso(task.due_at)
        if types and task.type not in types:
            continue
        if states and task.state not in states:
            continue
        if course_ids and task.course_id not in course_ids:
            continue
        if active_courses_only and task.course_id not in active_courses:
            continue
        if overdue is not None and task_is_overdue(task, now) != overdue:
            continue
        if (due_after or due_before) and due_at is None:
            continue
        if due_after and due_at <= due_after:
            continue
        if due_before and due_at >= due_before:
            continue
        selected.append(task)
    far_future = datetime.max.replace(tzinfo=CHINA_TZ)
    selected.sort(key=lambda task: (parse_iso(task.due_at) or far_future, task.id))
    return selected


def course_to_api(course: Course, now: datetime) -> dict[str, Any]:
    return {
        "id": course.id,
        "name": course.name,
        "term": course.term,
        "start_at": course.start_at,
        "end_at": course.end_at,
        "active": course_is_active(course, now),
    }


def task_to_api(task: Task, now: datetime) -> dict[str, Any]:
    return {
        "id": task.id,
        "type": task.type,
        "course": {"id": task.course_id, "name": task.course_name},
        "title": task.title,
        "state": task.state,
        "due_at": task.due_at,
        "published_at": task.published_at,
        "first_seen_at": task.first_seen_at,
        "overdue": task_is_overdue(task, now),
        "late_submission_allowed": task.late_submission_allowed,
        "score": task.score,
        "total_score": task.total_score,
        "deliverables": list(task.deliverables),
        "url": task.url,
        "upstream": dict(task.upstream),
    }


def request_json(client: EduplusClient, path: str) -> dict[str, Any]:
    try:
        return client.api_json(path)
    except urllib.error.HTTPError as exc:
        # EDUPLUS answers 401 with a JSON body that carries the business code.
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            return data
        raise UpstreamError(f"{path} 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        raise UpstreamError(f"{path} 请求失败：{exc}") from exc


def unwrap(data: dict[str, Any], what: str) -> Any:
    code = data.get("code")
    if code in UPSTREAM_SESSION_EXPIRED_CODES:
        raise SessionExpiredError(f"{what}：登录态已失效", upstream_code=code)
    if code != UPSTREAM_OK:
        raise UpstreamError(f"{what}返回异常：{code} {data.get('message') or ''}".strip(), upstream_code=code if isinstance(code, int) else None)
    return data.get("data")


def check_session(client: EduplusClient) -> SessionCheck:
    data = request_json(client, "/api/login/verify")
    code = data.get("code")
    return SessionCheck(valid=code == UPSTREAM_OK, upstream_code=code if isinstance(code, int) else None)


def fetch_courses(client: EduplusClient) -> list[Course]:
    rows = unwrap(request_json(client, "/api/course/athena/courses/study?types=Theory%2CTrain"), "课程列表")
    if not isinstance(rows, list):
        raise UpstreamError("课程列表格式无法识别")
    return [course for course in (normalize_course(row) for row in rows if isinstance(row, dict)) if course]


def fetch_homeworks(client: EduplusClient, course: Course) -> list[Task]:
    path = f"/api/course/homeworks/published/student?courseId={urllib.parse.quote(course.id)}"
    rows = unwrap(request_json(client, path), "作业列表")
    if not isinstance(rows, list):
        raise UpstreamError("作业列表格式无法识别")
    return [task for task in (normalize_homework(row, course, client.base_url) for row in rows if isinstance(row, dict)) if task]


def fetch_teach_class_id(client: EduplusClient, course: Course, user_id: str) -> str:
    query = urllib.parse.urlencode({"courseId": course.id, "studentId": user_id})
    data = unwrap(request_json(client, f"/api/course/course_teach_class?{query}"), "教学班")
    rows = data.get("rows") if isinstance(data, dict) else None
    if not rows or not isinstance(rows[0], dict):
        return ""
    return str(rows[0].get("id") or "")


def fetch_experiments(client: EduplusClient, course: Course, user_id: str, teach_class_id: str) -> list[Task]:
    if not teach_class_id:
        return []
    query = urllib.parse.urlencode({"userId": user_id, "details": "report,experiment,usedTimes,experiment.other"})
    path = f"/api/course/teach_classes/{urllib.parse.quote(teach_class_id)}/experiments?{query}"
    data = unwrap(request_json(client, path), "实验列表")
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise UpstreamError("实验列表格式无法识别")
    total = data.get("total")
    if isinstance(total, int) and total > len(rows):
        raise UpstreamError(f"实验列表不完整：共 {total} 个，只返回了 {len(rows)} 个")
    return [task for task in (normalize_experiment(row, course, client.base_url) for row in rows if isinstance(row, dict)) if task]


def fetch_status(client: EduplusClient, teach_class_cache: dict[str, str] | None = None) -> FetchResult:
    """Fetch every course's homeworks and experiments.

    A failure inside one course is recorded in `failures` and does not stop the
    scan; an expired session or a failed course list raises.
    """
    cache = teach_class_cache if teach_class_cache is not None else {}
    user = unwrap(request_json(client, "/api/portal/users/basic_info"), "用户信息")
    user_id = str(user.get("userid") or "") if isinstance(user, dict) else ""
    if not user_id:
        raise UpstreamError("用户信息里没有 userid")

    courses = fetch_courses(client)
    tasks: list[Task] = []
    failures: list[ScanFailure] = []
    for course in courses:
        try:
            tasks.extend(fetch_homeworks(client, course))
        except SessionExpiredError:
            raise
        except UpstreamError as exc:
            failures.append(ScanFailure(course_id=course.id, type="homework", error=str(exc)))
        try:
            if course.id not in cache:
                cache[course.id] = fetch_teach_class_id(client, course, user_id)
            tasks.extend(fetch_experiments(client, course, user_id, cache[course.id]))
        except SessionExpiredError:
            raise
        except UpstreamError as exc:
            failures.append(ScanFailure(course_id=course.id, type="experiment", error=str(exc)))
    return FetchResult(courses=courses, tasks=tasks, failures=failures)
