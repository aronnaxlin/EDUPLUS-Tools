from __future__ import annotations

from eduplus_tools.features.homework import infer_homework_done, infer_json_status_folder


def make_item(sub_status: int, submit_task: int) -> dict:
    return {"submitTask": submit_task, "homeworkDTO": {"subStatus": sub_status}}


def test_infer_homework_done_submitted_ignores_submit_task() -> None:
    # Submitted, waiting for review: the page shows "撤回作业".
    assert infer_homework_done(make_item(sub_status=1, submit_task=0))


def test_infer_homework_done_graded() -> None:
    assert infer_homework_done(make_item(sub_status=3, submit_task=1))


def test_infer_homework_done_unsubmitted_ignores_submit_task() -> None:
    # Answers saved but never submitted: the page still shows "去做作业".
    assert not infer_homework_done(make_item(sub_status=0, submit_task=1))


def test_infer_homework_done_missing_dto() -> None:
    assert not infer_homework_done({"submitTask": 1})


def test_infer_json_status_folder_prefers_sub_status_over_stale_folder() -> None:
    data = {"status_folder": "做过", "submitTask": 1, "subStatus": 0}
    assert infer_json_status_folder(data) == "没做过"


def test_infer_json_status_folder_keeps_folder_without_sub_status() -> None:
    assert infer_json_status_folder({"status_folder": "做过"}) == "做过"
