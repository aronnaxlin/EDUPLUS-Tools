from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Literal

from ..core.client import EduplusClient
from ..core.config import safe_filename

HomeworkAnswerMode = Literal["plain", "answers", "both"]
HomeworkStatusMode = Literal["all", "done", "undone"]
HOMEWORK_STATUS_MODE_LABELS = {
    "all": "两者都要",
    "done": "做过的作业",
    "undone": "未做的作业",
}


def normalize_answer_mode(value: str | None) -> HomeworkAnswerMode:
    mode = (value or "plain").strip().lower()
    aliases = {
        "no": "plain",
        "none": "plain",
        "without": "plain",
        "without-answers": "plain",
        "plain": "plain",
        "with": "answers",
        "answer": "answers",
        "answers": "answers",
        "with-answers": "answers",
        "all": "both",
        "both": "both",
    }
    resolved = aliases.get(mode)
    if resolved not in {"plain", "answers", "both"}:
        raise ValueError(f"Unsupported homework answer mode: {value}")
    return resolved  # type: ignore[return-value]


def normalize_status_mode(value: str | None) -> HomeworkStatusMode:
    mode = (value or "all").strip().lower()
    aliases = {
        "all": "all",
        "both": "all",
        "any": "all",
        "全部": "all",
        "两者都要": "all",
        "done": "done",
        "finished": "done",
        "complete": "done",
        "completed": "done",
        "submitted": "done",
        "answered": "done",
        "做过": "done",
        "已做": "done",
        "做过的作业": "done",
        "undone": "undone",
        "unfinished": "undone",
        "incomplete": "undone",
        "unsubmitted": "undone",
        "unanswered": "undone",
        "not-done": "undone",
        "not_done": "undone",
        "未做": "undone",
        "没做过": "undone",
        "未做的作业": "undone",
    }
    resolved = aliases.get(mode)
    if resolved not in {"all", "done", "undone"}:
        raise ValueError(f"Unsupported homework status mode: {value}")
    return resolved  # type: ignore[return-value]


def is_done_folder(is_done: bool) -> str:
    return "做过" if is_done else "没做过"


def homework_matches_status_mode(is_done: bool, status_mode: HomeworkStatusMode) -> bool:
    if status_mode == "all":
        return True
    return is_done if status_mode == "done" else not is_done


ANSWER_RELATED_KEYS = {"answer", "userAnswer", "isCorrect", "userScore", "hwAnswerId"}


def strip_answer_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strip_answer_fields(item) for key, item in value.items() if key not in ANSWER_RELATED_KEYS}
    if isinstance(value, list):
        return [strip_answer_fields(item) for item in value]
    return value


def infer_homework_done(item: dict[str, Any]) -> bool:
    submit_task = item.get("submitTask")
    if submit_task is not None:
        return str(submit_task) == "1"

    homework = item.get("homeworkDTO", {})
    if not isinstance(homework, dict):
        return False
    if homework.get("userScore") is not None:
        return True
    sub_status = homework.get("subStatus")
    return str(sub_status) in {"1", "2", "3"}


def infer_questions_done(questions: list[dict[str, Any]], fallback: bool) -> bool:
    answer_values = [question.get("isAnswer") for question in questions if question.get("isAnswer") is not None]
    if not answer_values:
        return fallback
    return all(str(value) == "1" for value in answer_values)


def html_to_md(text: Any) -> str:
    """Convert HTML question/option content to Markdown."""
    if text is None:
        return ""
    text = unescape(str(text))
    text = re.sub(r"<br\s*/?>", "  \n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>',
        lambda m: f"\n![图片]({m.group(1)})\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<img[^>]*>", "\n![图片]()\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def clean_html(text: Any) -> str:
    """Strip HTML to plain text (used for answer formatting)."""
    if text is None:
        return ""
    text = unescape(str(text))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>', "[图片]", text, flags=re.IGNORECASE)
    text = re.sub(r"<img[^>]*>", "[图片]", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def get_homework_list(client: EduplusClient, course_id: str, log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    path = f"/api/course/homeworks/published/student?courseId={urllib.parse.quote(course_id)}"
    data = client.api_json(path)
    if not data.get("success") or "data" not in data:
        log("作业列表接口返回异常。")
        return []

    homework_items = []
    for item in data["data"]:
        if not isinstance(item, dict):
            continue
        homework = item.get("homeworkDTO", {})
        if not isinstance(homework, dict):
            continue
        if "id" in homework and "name" in homework:
            is_done = infer_homework_done(item)
            homework_items.append(
                {
                    "sequence": item.get("sequence", 0),
                    "name": homework["name"],
                    "id": homework["id"],
                    "is_done": is_done,
                    "status_folder": is_done_folder(is_done),
                    "submitTask": item.get("submitTask"),
                    "status": homework.get("status"),
                    "subStatus": homework.get("subStatus"),
                    "userScore": homework.get("userScore"),
                    "totalScore": homework.get("totalScore"),
                }
            )

    homework_items.sort(key=lambda x: x["sequence"])
    return [
        {
            "name": item["name"],
            "id": item["id"],
            "is_done": item["is_done"],
            "status_folder": item["status_folder"],
            "submitTask": item.get("submitTask"),
            "status": item.get("status"),
            "subStatus": item.get("subStatus"),
            "userScore": item.get("userScore"),
            "totalScore": item.get("totalScore"),
        }
        for item in homework_items
    ]


def get_question_detail(client: EduplusClient, question_id: str, log: Callable[[str], None] = print) -> dict[str, Any] | None:
    path = f"/api/course/homeworkQuestions/{urllib.parse.quote(question_id)}/student/detail"
    data = client.api_json(path)
    if data.get("code") not in [2000000, "OK"]:
        log(f"题目详情接口错误：{data.get('message')}")
        return None
    detail = data.get("data")
    return detail if isinstance(detail, dict) else None


def get_sorted_questions(client: EduplusClient, homework_id: str, log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    path = f"/api/course/homeworkQuestions/student?homeworkId={urllib.parse.quote(homework_id)}"
    data = client.api_json(path)
    if data.get("code") not in [2000000, "OK"]:
        log(f"题目列表接口错误：{data.get('message')}")
        return []

    questions = data.get("data", [])
    if not isinstance(questions, list):
        return []

    sorted_questions = sorted(questions, key=lambda q: int(q.get("orderNumber", 99999)))
    detailed_questions = []
    for question in sorted_questions:
        question_id = str(question.get("id") or "")
        if not question_id:
            continue

        detail = get_question_detail(client, question_id, log=log)
        if detail:
            question["detail"] = detail
            detailed_questions.append(question)
        time.sleep(0.3)

    return detailed_questions


def process_homework(
    client: EduplusClient,
    homework: dict[str, Any],
    output_dir: Path,
    *,
    include_answers_json: bool,
    status_mode: HomeworkStatusMode = "all",
    log: Callable[[str], None] = print,
) -> Path | None:
    homework_name = str(homework["name"])
    homework_id = str(homework["id"])
    is_done = bool(homework.get("is_done"))
    safe_name = safe_filename(homework_name, "homework")
    questions = get_sorted_questions(client, homework_id, log=log)
    if not questions:
        log(f"无法获取《{homework_name}》的题目详情。")
        return None
    is_done = infer_questions_done(questions, is_done)
    status_folder = is_done_folder(is_done)
    if not homework_matches_status_mode(is_done, status_mode):
        log(f"已跳过《{homework_name}》：题目详情判定为{status_folder}，不在当前作业范围内。")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / status_folder / f"作业_{safe_name}_{timestamp}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    homework_data = {
        "homework_name": homework_name,
        "homework_id": homework_id,
        "is_done": is_done,
        "status_folder": status_folder,
        "submitTask": homework.get("submitTask"),
        "status": homework.get("status"),
        "subStatus": homework.get("subStatus"),
        "userScore": homework.get("userScore"),
        "totalScore": homework.get("totalScore"),
        "timestamp": datetime.now().isoformat(),
        "question_count": len(questions),
        "questions": questions,
    }
    if not include_answers_json:
        homework_data = strip_answer_fields(homework_data)
    json_path.write_text(
        json.dumps(
            homework_data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return json_path


def split_answer_tokens(answer: Any) -> list[str]:
    if answer is None:
        return []
    if isinstance(answer, list):
        return [str(item).strip() for item in answer if str(item).strip()]

    answer_text = str(answer).strip()
    if not answer_text:
        return []
    if "," in answer_text:
        return [token.strip() for token in answer_text.split(",") if token.strip()]
    if re.fullmatch(r"[A-Za-z]+", answer_text):
        return list(answer_text.upper())
    return [answer_text]


def format_answer_value(detail: dict[str, Any], answer_value: Any) -> str:
    if answer_value in (None, ""):
        return "(未提供)"

    qsn_type = detail.get("qsnType")
    if qsn_type == 3:
        mapping = {"true": "正确", "false": "错误"}
        return mapping.get(str(answer_value).strip().lower(), str(answer_value))

    if qsn_type in [1, 2]:
        option_map = {}
        for opt in detail.get("options", []):
            option_id = str(opt.get("id", "")).strip().upper()
            option_content = clean_html(opt.get("optionContent", ""))
            if option_id:
                option_map[option_id] = option_content

        formatted_options = []
        for token in split_answer_tokens(answer_value):
            option_id = token.upper()
            option_content = option_map.get(option_id)
            formatted_options.append(f"{option_id}. {option_content}" if option_content else token)
        return "；".join(formatted_options) if formatted_options else str(answer_value)

    if qsn_type == 6:
        tokens = split_answer_tokens(answer_value)
        return "；".join(tokens) if tokens else str(answer_value)

    return str(answer_value)


def get_question_type_label(qsn_type: Any) -> str | None:
    return {1: "单选题", 2: "多选题", 3: "判断题", 6: "填空题"}.get(qsn_type)


def write_md_output(data: dict[str, Any], md_path: Path, include_answers: bool = False) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    with md_path.open("w", encoding="utf-8") as f:
        homework_name = data.get("homework_name", "未知作业")
        f.write(f"# {homework_name}\n\n")
        f.write(f"**题目数量：** {data.get('question_count', 0)}  \n")
        if "is_done" in data:
            f.write(f"**作答状态：** {is_done_folder(bool(data.get('is_done')))}  \n")
        f.write(f"**导出时间：** {data.get('timestamp', '')}  \n")
        if include_answers:
            f.write("**导出类型：** 带答案版本  \n")
        f.write("\n---\n\n")

        for idx, question in enumerate(data.get("questions", [])):
            detail = question.get("detail", {})
            qsn_type = detail.get("qsnType")
            title = html_to_md(detail.get("titleText", ""))
            question_type_label = get_question_type_label(qsn_type)

            heading = f"题目 {idx + 1}"
            if question_type_label:
                heading += f"（{question_type_label}）"
            f.write(f"## {heading}\n\n")
            f.write(f"{title}\n\n")

            if qsn_type in [1, 2]:
                for opt_idx, opt in enumerate(detail.get("options", [])):
                    content = html_to_md(opt.get("optionContent", ""))
                    f.write(f"- {chr(65 + opt_idx)}. {content}\n")
                f.write("\n")
            elif qsn_type not in [3, 6]:
                f.write(f"*(未知题型: {qsn_type})*\n\n")

            if include_answers:
                user_answer = detail.get("userAnswer")
                user_ans_str = format_answer_value(detail, user_answer) if user_answer not in (None, "") else "*(未作答)*"
                f.write(f"> **用户答案：** {user_ans_str}  \n")
                correct_answer = detail.get("answer")
                if correct_answer not in (None, ""):
                    f.write(f"> **正确答案：** {format_answer_value(detail, correct_answer)}  \n")
                if "isCorrect" in detail:
                    result = "正确" if detail.get("isCorrect") == 1 else "错误"
                    f.write(f"> **判题结果：** {result}  \n")
                score = detail.get("userScore", question.get("userScore"))
                if score is not None:
                    f.write(f"> **得分：** {score}  \n")
                f.write("\n")

            f.write("---\n\n")


def infer_json_status_folder(data: dict[str, Any]) -> str:
    if "status_folder" in data:
        folder = str(data.get("status_folder") or "")
        if folder in {"做过", "没做过"}:
            return folder
    if "is_done" in data:
        return is_done_folder(bool(data.get("is_done")))

    questions = data.get("questions", [])
    if isinstance(questions, list) and questions:
        answer_values = [question.get("isAnswer") for question in questions if isinstance(question, dict)]
        if answer_values:
            return is_done_folder(all(str(value) == "1" for value in answer_values))
    return "没做过"


def convert_to_md(
    json_path: Path,
    output_dir: Path,
    *,
    answer_mode: HomeworkAnswerMode = "plain",
    log: Callable[[str], None] = print,
) -> list[Path]:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        base_name = json_path.stem
        status_folder = infer_json_status_folder(data)
        generated_paths = []

        if answer_mode in {"plain", "both"}:
            plain_path = output_dir / status_folder / f"{base_name}.md"
            write_md_output(data, plain_path, include_answers=False)
            generated_paths.append(plain_path)
            log(f"已生成不带答案 Markdown：{plain_path}")

        if answer_mode in {"answers", "both"}:
            answer_path = output_dir / status_folder / f"{base_name}_带答案.md"
            write_md_output(data, answer_path, include_answers=True)
            generated_paths.append(answer_path)
            log(f"已生成带答案 Markdown：{answer_path}")

        return generated_paths
    except Exception as exc:
        log(f"转换失败：{json_path}：{exc}")
        return []


def json_file_matches_status_mode(json_path: Path, status_mode: HomeworkStatusMode) -> bool:
    if status_mode == "all":
        return True

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        status_folder = infer_json_status_folder(data)
    except Exception:
        status_folder = json_path.parent.name

    return homework_matches_status_mode(status_folder == "做过", status_mode)


def scrape_homework(
    client: EduplusClient,
    *,
    course_id: str,
    output_root: Path,
    convert_existing: bool = True,
    answer_mode: HomeworkAnswerMode = "plain",
    status_mode: HomeworkStatusMode = "all",
    log: Callable[[str], None] = print,
) -> int:
    answer_mode = normalize_answer_mode(answer_mode)
    status_mode = normalize_status_mode(status_mode)
    json_dir = output_root / "homework" / "json"
    md_dir = output_root / "homework" / "markdown"
    json_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)
    for status_folder in ("做过", "没做过"):
        (json_dir / status_folder).mkdir(parents=True, exist_ok=True)
        (md_dir / status_folder).mkdir(parents=True, exist_ok=True)

    homeworks = get_homework_list(client, course_id, log=log)
    if not homeworks:
        log("未找到作业，请检查 SESSION、课程 ID 或网络。")
        return 1

    answer_mode_label = {"plain": "不带答案", "answers": "带答案", "both": "两者都要"}[answer_mode]
    log(f"作业答案导出：{answer_mode_label}")
    log(f"作业范围：{HOMEWORK_STATUS_MODE_LABELS[status_mode]}")
    done_count = sum(1 for homework in homeworks if homework.get("is_done"))
    undone_count = len(homeworks) - done_count
    log(f"找到 {len(homeworks)} 份作业：做过 {done_count} 份，没做过 {undone_count} 份。")
    selected_homeworks = [homework for homework in homeworks if homework_matches_status_mode(bool(homework.get("is_done")), status_mode)]
    if len(selected_homeworks) != len(homeworks):
        log(f"本次将处理 {len(selected_homeworks)} 份符合范围的作业。")
    if not selected_homeworks:
        log("没有符合当前范围的作业。")

    json_files = []
    for homework in selected_homeworks:
        log(f"\n正在处理作业：{homework['name']}（{homework.get('status_folder')}）")
        json_path = process_homework(
            client,
            homework,
            json_dir,
            include_answers_json=answer_mode in {"answers", "both"},
            status_mode=status_mode,
            log=log,
        )
        if json_path:
            json_files.append(json_path)
            log(f"已保存 JSON：{json_path}")
        time.sleep(1)

    log("\n正在把 JSON 转成 Markdown...")
    for json_file in json_files:
        convert_to_md(json_file, md_dir, answer_mode=answer_mode, log=log)

    if convert_existing:
        for json_file in json_dir.rglob("*.json"):
            if json_file not in json_files:
                if not json_file_matches_status_mode(json_file, status_mode):
                    continue
                log(f"正在转换已有文件：{json_file.name}")
                convert_to_md(json_file, md_dir, answer_mode=answer_mode, log=log)

    log("\n处理完成。")
    log(f"JSON 目录：{json_dir}")
    log(f"Markdown 目录：{md_dir}")
    return 0
