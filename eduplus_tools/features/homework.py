from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

from ..core.client import EduplusClient
from ..core.config import safe_filename


def clean_html(text: Any) -> str:
    if text is None:
        return ""
    text = unescape(str(text))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def get_homework_list(client: EduplusClient, course_id: str, log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    path = f"/api/course/homeworks/published/student?courseId={urllib.parse.quote(course_id)}"
    data = client.api_json(path)
    if not data.get("success") or "data" not in data:
        log("Error: homework list API response is invalid.")
        return []

    homework_items = []
    for item in data["data"]:
        homework = item.get("homeworkDTO", {})
        if "id" in homework and "name" in homework:
            homework_items.append({"sequence": item.get("sequence", 0), "name": homework["name"], "id": homework["id"]})

    homework_items.sort(key=lambda x: x["sequence"])
    return [{"name": item["name"], "id": item["id"]} for item in homework_items]


def get_question_detail(client: EduplusClient, question_id: str, log: Callable[[str], None] = print) -> dict[str, Any] | None:
    path = f"/api/course/homeworkQuestions/{urllib.parse.quote(question_id)}/student/detail"
    data = client.api_json(path)
    if data.get("code") not in [2000000, "OK"]:
        log(f"Question detail API error: {data.get('message')}")
        return None
    detail = data.get("data")
    return detail if isinstance(detail, dict) else None


def get_sorted_questions(client: EduplusClient, homework_id: str, log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    path = f"/api/course/homeworkQuestions/student?homeworkId={urllib.parse.quote(homework_id)}"
    data = client.api_json(path)
    if data.get("code") not in [2000000, "OK"]:
        log(f"Questions API error: {data.get('message')}")
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
    log: Callable[[str], None] = print,
) -> Path | None:
    homework_name = str(homework["name"])
    homework_id = str(homework["id"])
    safe_name = safe_filename(homework_name, "homework")
    questions = get_sorted_questions(client, homework_id, log=log)
    if not questions:
        log(f"Could not fetch questions for '{homework_name}'.")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"作业_{safe_name}_{timestamp}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "homework_name": homework_name,
                "homework_id": homework_id,
                "timestamp": datetime.now().isoformat(),
                "question_count": len(questions),
                "questions": questions,
            },
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


def write_text_output(data: dict[str, Any], text_path: Path, include_answers: bool = False) -> None:
    text_path.parent.mkdir(parents=True, exist_ok=True)
    with text_path.open("w", encoding="utf-8") as out_f:
        homework_name = data.get("homework_name", "未知作业")
        out_f.write(f"作业名称: {homework_name}\n")
        out_f.write(f"题目数量: {data.get('question_count', 0)}\n")
        out_f.write(f"导出时间: {data.get('timestamp', '')}\n")
        if include_answers:
            out_f.write("导出类型: 带答案版本\n")
        out_f.write("=" * 60 + "\n\n")

        for idx, question in enumerate(data.get("questions", [])):
            detail = question.get("detail", {})
            qsn_type = detail.get("qsnType")
            title = clean_html(detail.get("titleText", ""))
            question_type_label = get_question_type_label(qsn_type)

            out_f.write(f"题目 {idx + 1}: {title}\n")
            if question_type_label:
                out_f.write(f"  ({question_type_label})\n")

            if qsn_type in [1, 2]:
                for opt_idx, opt in enumerate(detail.get("options", [])):
                    content = clean_html(opt.get("optionContent", ""))
                    out_f.write(f"  {chr(65 + opt_idx)}. {content}\n")
            elif qsn_type not in [3, 6]:
                out_f.write(f"  (未知题型: {qsn_type})\n")

            if include_answers:
                user_answer = detail.get("userAnswer")
                out_f.write(f"  用户答案: {format_answer_value(detail, user_answer) if user_answer not in (None, '') else '(未作答)'}\n")
                correct_answer = detail.get("answer")
                if correct_answer not in (None, ""):
                    out_f.write(f"  正确答案: {format_answer_value(detail, correct_answer)}\n")
                if "isCorrect" in detail:
                    out_f.write(f"  判题结果: {'正确' if detail.get('isCorrect') == 1 else '错误'}\n")
                score = detail.get("userScore", question.get("userScore"))
                if score is not None:
                    out_f.write(f"  得分: {score}\n")

            out_f.write("\n")


def convert_to_text(json_path: Path, output_dir: Path, log: Callable[[str], None] = print) -> Path | None:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        base_name = json_path.stem
        plain_path = output_dir / "不带答案" / f"{base_name}.txt"
        answer_path = output_dir / "带答案" / f"{base_name}_带答案.txt"
        write_text_output(data, plain_path, include_answers=False)
        write_text_output(data, answer_path, include_answers=True)
        log(f"Created text file: {plain_path}")
        log(f"Created answer text file: {answer_path}")
        return plain_path
    except Exception as exc:
        log(f"Failed to convert {json_path}: {exc}")
        return None


def scrape_homework(
    client: EduplusClient,
    *,
    course_id: str,
    output_root: Path,
    convert_existing: bool = True,
    log: Callable[[str], None] = print,
) -> int:
    json_dir = output_root / "homework" / "json"
    text_dir = output_root / "homework" / "text"
    json_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    homeworks = get_homework_list(client, course_id, log=log)
    if not homeworks:
        log("No homework found. Check config and network access.")
        return 1

    log(f"Found {len(homeworks)} homework item(s).")
    json_files = []
    for homework in homeworks:
        log(f"\nProcessing homework: {homework['name']}")
        json_path = process_homework(client, homework, json_dir, log=log)
        if json_path:
            json_files.append(json_path)
            log(f"Saved JSON file: {json_path}")
        time.sleep(1)

    log("\nConverting JSON files to text...")
    for json_file in json_files:
        convert_to_text(json_file, text_dir, log=log)

    if convert_existing:
        for json_file in json_dir.glob("*.json"):
            if json_file not in json_files:
                log(f"Converting existing file: {json_file.name}")
                convert_to_text(json_file, text_dir, log=log)

    log("\nDone.")
    log(f"JSON directory: {json_dir}")
    log(f"Text directory: {text_dir}")
    return 0
