from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from eduplus_tools.features.status import (
    CHINA_TZ,
    Course,
    FetchResult,
    ScanFailure,
    SessionCheck,
    Task,
    UpstreamError,
)


BASE_URL = "https://www.eduplus.net"


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=CHINA_TZ).timestamp() * 1000)


def homework_item(publish_id: str = "pub1", sub_status: int = 0, submit_task: int = 0, end: str = "2026-10-15T23:59:59") -> dict:
    return {
        "id": publish_id,
        "endTime": ms(end),
        "createTime": ms("2026-10-09T10:20:59"),
        "submitTask": submit_task,
        "delaySubmit": 1,
        "homeworkDTO": {"id": "hw1", "name": "第一章 引论 作业", "subStatus": sub_status, "status": 1, "totalScore": 100},
    }


def experiment_item(publish_id: str = "exp1", reports: list | None = None) -> dict:
    return {
        "id": publish_id,
        "teachClassId": "tc1",
        "testcaseId": "case1",
        "submitTime": ms("2026-10-12T00:00:00"),
        "reportRequired": "Yes",
        "codeRequired": "No",
        "status": "Publish",
        "experiment": {"id": "case1", "name": "框架实验一：搭建开发环境"},
        "experimentReports": reports or [],
    }


def course(course_id: str = "c1", end: str = "2027-01-31T23:59:59") -> Course:
    return Course(id=course_id, name=f"课程{course_id}", term="2026-2027第一学期", start_at="2026-09-01T00:00:00+08:00", end_at=f"{end}+08:00")


def task(task_id: str = "homework:pub1", *, course_id: str = "c1", state: str = "not_submitted", due_at: str | None = "2026-10-20T23:59:59+08:00") -> Task:
    task_type = task_id.split(":", 1)[0]
    return Task(
        id=task_id,
        type=task_type,  # type: ignore[arg-type]
        course_id=course_id,
        course_name=f"课程{course_id}",
        title=f"任务 {task_id}",
        state=state,  # type: ignore[arg-type]
        url=f"{BASE_URL}/course/course/{course_id}/HomeworkPage?userRole=",
        due_at=due_at,
    )


@dataclass
class FakeUpstream:
    courses: list[Course] = field(default_factory=lambda: [course()])
    tasks: list[Task] = field(default_factory=list)
    failures: list[ScanFailure] = field(default_factory=list)
    session_valid: bool = True
    broken: bool = False

    def check_session(self) -> SessionCheck:
        if self.broken:
            raise UpstreamError("网络不可用")
        return SessionCheck(valid=self.session_valid, upstream_code=2000000 if self.session_valid else 4010000)

    def fetch(self, teach_class_cache: dict[str, str]) -> FetchResult:
        # Return copies so the monitor cannot mutate the fake's state between scans.
        return FetchResult(
            courses=list(self.courses),
            tasks=[Task(**{**vars(item), "deliverables": list(item.deliverables), "upstream": dict(item.upstream)}) for item in self.tasks],
            failures=list(self.failures),
        )


class FakeClock:
    def __init__(self, start: str = "2026-10-15T12:00:00") -> None:
        self.value = datetime.fromisoformat(start).replace(tzinfo=CHINA_TZ)

    def __call__(self) -> datetime:
        return self.value
