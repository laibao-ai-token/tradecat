from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import src.__main__ as main_mod
from src.__main__ import Scheduler


def test_scheduler_add_records_process_definition() -> None:
    sched = Scheduler()
    sched.add("ws", ["python", "-m", "collectors.ws"])

    assert "ws" in sched._procs
    assert sched._procs["ws"]["cmd"] == ["python", "-m", "collectors.ws"]
    assert sched._procs["ws"]["restarts"] == 0
    assert sched._procs["ws"]["proc"] is None


def test_scheduler_request_stop_sets_flags() -> None:
    sched = Scheduler()
    sched._running = True

    sched._request_stop()

    assert not sched._running
    assert sched._stop_event.is_set()


def test_scheduler_start_spawns_subprocess_with_service_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sched = Scheduler()
    info = {"cmd": ["python", "-m", "collectors.ws"], "proc": None, "restarts": 0}
    monkeypatch.setattr(main_mod.settings, "log_dir", tmp_path)

    captured: dict[str, object] = {}

    def _fake_popen(cmd, stdout, stderr, cwd):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["stdout_name"] = getattr(stdout, "name", "")
        captured["stderr"] = stderr
        return SimpleNamespace(pid=321)

    monkeypatch.setattr(main_mod.subprocess, "Popen", _fake_popen)

    sched._start("ws", info)

    assert info["proc"].pid == 321
    assert captured["cmd"] == ["python", "-m", "collectors.ws"]
    assert captured["cwd"] == str(main_mod.SERVICE_SRC_DIR)
    assert str(captured["stdout_name"]).endswith("ws.log")
    assert captured["stderr"] == main_mod.subprocess.STDOUT
