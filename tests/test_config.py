from __future__ import annotations

import json

import pytest

from eduplus_tools.core.config import (
    clear_runtime_overrides,
    load_config,
    save_persisted_credentials,
    set_runtime_overrides,
)
from eduplus_tools.web import jobs


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    clear_runtime_overrides()
    for name in ("EDUPLUS_SESSION", "EDUPLUS_COURSE_ID", "EDUPLUS_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EDUPLUS_DATA_DIR", str(tmp_path / "data"))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"session": "file-token", "course_id": "c-file"}), encoding="utf-8")
    yield config_path
    clear_runtime_overrides()


def test_load_config_uses_server_defaults_by_default(_isolate, monkeypatch):
    monkeypatch.setenv("EDUPLUS_SESSION", "env-token")
    config = load_config(config_file=_isolate, require_course_id=False)
    assert config.session == "env-token"


def test_load_config_without_server_defaults_ignores_file_env_and_runtime(_isolate, monkeypatch):
    monkeypatch.setenv("EDUPLUS_SESSION", "env-token")
    set_runtime_overrides(session="runtime-token")
    with pytest.raises(SystemExit):
        load_config(config_file=_isolate, require_course_id=False, use_server_defaults=False)

    config = load_config(
        config_file=_isolate,
        session="visitor-token",
        require_course_id=False,
        use_server_defaults=False,
    )
    assert config.session == "visitor-token"
    assert config.course_id == ""
    assert config.config_path is None


def test_public_job_never_borrows_server_session(monkeypatch, tmp_path):
    monkeypatch.setenv("EDUPLUS_SESSION", "owner-token")
    set_runtime_overrides(session="owner-runtime-token")
    monkeypatch.setattr(jobs, "_job_output_root", lambda payload, job_id: tmp_path / job_id)
    store = jobs.JobStore()
    job = store.create("ppt", "public")

    jobs._run_job(store, job.id, {"command": "ppt", "execution_mode": "public", "course_id": "c1"})

    finished = store.get(job.id)
    assert finished.status == "failed"
    assert any("session" in line for line in finished.logs)


def test_persisted_credentials_beat_env_until_env_changes(_isolate, monkeypatch):
    monkeypatch.setenv("EDUPLUS_SESSION", "env-old")
    _, persisted, error = save_persisted_credentials(session="mcp-new")
    assert persisted and error is None
    assert load_config(config_file=_isolate, require_course_id=False).session == "mcp-new"

    # Operator edits .env afterwards: the newer env value wins again.
    monkeypatch.setenv("EDUPLUS_SESSION", "env-newer")
    assert load_config(config_file=_isolate, require_course_id=False).session == "env-newer"


def test_public_mode_ignores_persisted_credentials(_isolate):
    save_persisted_credentials(session="mcp-new")
    with pytest.raises(SystemExit):
        load_config(config_file=_isolate, require_course_id=False, use_server_defaults=False)
