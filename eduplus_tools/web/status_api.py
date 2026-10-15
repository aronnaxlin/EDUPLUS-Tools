from __future__ import annotations

import hmac
import re
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from typing import Any, Protocol
from urllib.parse import ParseResult, parse_qs, unquote

from ..features.status import (
    TASK_STATES,
    TASK_TYPES,
    SessionExpiredError,
    UpstreamError,
    course_is_active,
    course_to_api,
    filter_tasks,
    parse_iso,
    task_to_api,
)
from ..monitor.service import StatusMonitor
from ..monitor.store import CursorExpiredError


EVENT_TYPES = (
    "task.published",
    "task.submitted",
    "task.graded",
    "task.withdrawn",
    "task.due_changed",
    "task.removed",
    "session.expired",
    "session.restored",
)
TASK_PATH = re.compile(r"^/api/v1/tasks/([^/]+)$")


class JsonResponder(Protocol):
    def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK, headers: dict[str, str] | None = None) -> None: ...


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def invalid(message: str) -> ApiError:
    return ApiError(HTTPStatus.BAD_REQUEST, "invalid_parameter", message)


class Query:
    def __init__(self, raw: str) -> None:
        self._values = parse_qs(raw, keep_blank_values=True)

    def enum_list(self, name: str, allowed: tuple[str, ...]) -> set[str]:
        values = set(self._values.get(name, []))
        unknown = values - set(allowed)
        if unknown:
            raise invalid(f"{name} 不支持：{', '.join(sorted(unknown))}")
        return values

    def strings(self, name: str) -> set[str]:
        return set(self._values.get(name, []))

    def boolean(self, name: str, default: bool | None = None) -> bool | None:
        values = self._values.get(name)
        if not values:
            return default
        value = values[-1].strip().lower()
        if value in {"true", "1"}:
            return True
        if value in {"false", "0"}:
            return False
        raise invalid(f"{name} 必须是 true 或 false")

    def datetime(self, name: str) -> datetime | None:
        values = self._values.get(name)
        if not values:
            return None
        # An unencoded "+08:00" arrives as " 08:00" after query decoding.
        raw = values[-1].strip().replace(" ", "+")
        try:
            return parse_iso(raw)
        except ValueError:
            raise invalid(f"{name} 不是有效的 ISO 8601 时间") from None

    def integer(self, name: str, default: int | None = None, minimum: int | None = None, maximum: int | None = None) -> int | None:
        values = self._values.get(name)
        if not values:
            return default
        try:
            value = int(values[-1])
        except ValueError:
            raise invalid(f"{name} 必须是整数") from None
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            raise invalid(f"{name} 超出范围")
        return value


class StatusApi:
    def __init__(self, monitor: StatusMonitor, token: str) -> None:
        self._monitor = monitor
        self._token = token

    def handle(self, handler: JsonResponder, method: str, parsed: ParseResult, authorization: str | None) -> None:
        try:
            self._authorize(authorization)
            payload, status = self._route(method, parsed)
        except ApiError as exc:
            headers = {"WWW-Authenticate": "Bearer"} if exc.status == HTTPStatus.UNAUTHORIZED else None
            handler._send_json({"error": exc.message, "code": exc.code}, status=exc.status, headers=headers)
            return
        handler._send_json(payload, status=status)

    def _authorize(self, authorization: str | None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), self._token.encode()):
            raise ApiError(HTTPStatus.UNAUTHORIZED, "unauthorized", "缺少或错误的访问 token")

    def _route(self, method: str, parsed: ParseResult) -> tuple[dict[str, Any], HTTPStatus]:
        path = parsed.path.rstrip("/")
        query = Query(parsed.query)
        if method == "GET" and path == "/api/v1/session":
            return self._session(), HTTPStatus.OK
        if method == "GET" and path == "/api/v1/courses":
            return self._courses(query), HTTPStatus.OK
        if method == "GET" and path == "/api/v1/tasks":
            return self._tasks(query), HTTPStatus.OK
        match = TASK_PATH.match(path)
        if method == "GET" and match:
            return self._task(unquote(match.group(1))), HTTPStatus.OK
        if method == "POST" and path == "/api/v1/scans":
            return self._monitor.start_scan("manual"), HTTPStatus.ACCEPTED
        if method == "GET" and path == "/api/v1/scans/latest":
            scan = self._monitor.latest_scan()
            if scan is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "还没有扫描记录")
            return scan, HTTPStatus.OK
        if method == "GET" and path == "/api/v1/events":
            return self._events(query), HTTPStatus.OK
        raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    def _session(self) -> dict[str, Any]:
        try:
            session = self._monitor.check_session()
            valid, upstream_code = session.valid, session.upstream_code
        except SessionExpiredError as exc:
            # Includes a missing SESSION in config.json: the session is simply not usable.
            valid, upstream_code = False, exc.upstream_code
        except UpstreamError as exc:
            raise ApiError(HTTPStatus.BAD_GATEWAY, "upstream_unavailable", str(exc)) from None
        return {
            "valid": valid,
            "checked_at": self._monitor.now().isoformat(timespec="seconds"),
            "upstream_code": upstream_code,
        }

    def _require_snapshot(self):
        snapshot = self._monitor.snapshot()
        if snapshot is None:
            raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "no_snapshot", "还没有完成过扫描")
        return snapshot

    def _courses(self, query: Query) -> dict[str, Any]:
        active_only = query.boolean("active", default=True)
        snapshot = self._require_snapshot()
        now = self._monitor.now()
        courses = [course for course in snapshot.courses if not active_only or course_is_active(course, now)]
        return {"items": [course_to_api(course, now) for course in courses], "scanned_at": snapshot.scanned_at}

    def _tasks(self, query: Query) -> dict[str, Any]:
        types = query.enum_list("type", TASK_TYPES)
        states = query.enum_list("state", TASK_STATES)
        course_ids = query.strings("course_id")
        active_only = query.boolean("active_courses_only", default=True)
        overdue = query.boolean("overdue")
        due_after = query.datetime("due_after")
        due_before = query.datetime("due_before")

        snapshot = self._require_snapshot()
        now = self._monitor.now()
        selected = filter_tasks(
            snapshot.tasks.values(),
            snapshot.courses,
            now,
            types=types,
            states=states,
            course_ids=course_ids,
            active_courses_only=bool(active_only),
            overdue=overdue,
            due_after=due_after,
            due_before=due_before,
        )
        return {
            "items": [task_to_api(task, now) for task in selected],
            "scanned_at": snapshot.scanned_at,
            "partial_failures": [asdict(failure) for failure in snapshot.failures],
        }

    def _task(self, task_id: str) -> dict[str, Any]:
        snapshot = self._require_snapshot()
        task = snapshot.tasks.get(task_id)
        if task is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "not_found", "未找到任务")
        return task_to_api(task, self._monitor.now())

    def _events(self, query: Query) -> dict[str, Any]:
        after = query.integer("after", minimum=0)
        limit = query.integer("limit", default=50, minimum=1, maximum=200)
        types = query.enum_list("type", EVENT_TYPES)
        try:
            items, next_cursor = self._monitor.store.list_events(after, limit or 50, sorted(types) or None)
        except CursorExpiredError:
            raise ApiError(HTTPStatus.GONE, "cursor_expired", "游标对应的事件已被清理，请重新同步") from None
        return {"items": items, "next_cursor": next_cursor}
