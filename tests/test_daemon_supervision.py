from __future__ import annotations

from pathlib import Path

import pytest

from vibe_maa import daemon
from vibe_maa.errors import DevError


def forbidden_spawn(*_args, **_kwargs):
    pytest.fail("This operation must not start or replace a daemon.")


def test_lost_session_owner_is_not_silently_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.subprocess, "Popen", forbidden_spawn)
    with pytest.raises(DevError) as caught:
        daemon.request(tmp_path, "observe", {"session_id": "retained-session"})
    assert caught.value.code == "SESSION_OWNER_LOST"
    assert not daemon._state_path(tmp_path).exists()


def test_session_list_does_not_start_a_service(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.subprocess, "Popen", forbidden_spawn)
    assert daemon.request(tmp_path, "session.list", {})["result"] == {
        "sessions": [],
        "daemon_running": False,
    }


def test_session_list_reports_dead_owner_without_replacing_it(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.subprocess, "Popen", forbidden_spawn)
    (tmp_path / ".maa-dev").mkdir()
    stale = {"pid": 999, "port": 1, "token": "expired-test-token"}
    daemon._write_state(tmp_path, stale)
    with pytest.raises(DevError) as caught:
        daemon.request(tmp_path, "session.list", {})
    assert caught.value.code == "DAEMON_UNAVAILABLE"
    assert daemon._read_state(tmp_path) == stale


def test_known_temporary_namespace_requires_a_supervisor(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "_command_scoped_pid_namespace", lambda: True)
    monkeypatch.setattr(daemon.subprocess, "Popen", forbidden_spawn)
    with pytest.raises(DevError) as caught:
        daemon.ensure_daemon(tmp_path)
    assert caught.value.code == "DAEMON_REQUIRES_SUPERVISOR"


def test_namespace_detection_is_narrow(monkeypatch):
    monkeypatch.setattr(daemon.sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_bytes", lambda _self: b"/usr/bin/tini\0--\0python\0")
    assert daemon._command_scoped_pid_namespace() is False
    monkeypatch.setattr(Path, "read_bytes", lambda _self: b"/opt/codex-linux-sandbox\0--\0")
    assert daemon._command_scoped_pid_namespace() is True


def external_environment(monkeypatch, project, pid="321"):
    monkeypatch.setenv("VIBE_MAA_EXTERNAL_DAEMON_PROJECT", str(project))
    monkeypatch.setenv("VIBE_MAA_EXTERNAL_DAEMON_PID", pid)
    monkeypatch.setattr(daemon.subprocess, "Popen", forbidden_spawn)


@pytest.mark.parametrize("pid", [None, "", "0", "-1", "not-a-pid"])
def test_external_policy_requires_a_complete_valid_contract(tmp_path, monkeypatch, pid):
    external_environment(monkeypatch, tmp_path, pid or "321")
    if pid is None:
        monkeypatch.delenv("VIBE_MAA_EXTERNAL_DAEMON_PID")
    else:
        monkeypatch.setenv("VIBE_MAA_EXTERNAL_DAEMON_PID", pid)
    with pytest.raises(DevError) as caught:
        daemon.ensure_daemon(tmp_path)
    assert caught.value.code == "EXTERNAL_DAEMON_CONFIGURATION"


def test_external_policy_rejects_other_project(tmp_path, monkeypatch):
    external_environment(monkeypatch, tmp_path)
    with pytest.raises(DevError) as caught:
        daemon.ensure_daemon(tmp_path / "other")
    assert caught.value.code == "EXTERNAL_DAEMON_PROJECT_MISMATCH"


def test_external_policy_never_replaces_a_dead_owner(tmp_path, monkeypatch):
    external_environment(monkeypatch, tmp_path)
    with pytest.raises(DevError) as caught:
        daemon.ensure_daemon(tmp_path)
    assert caught.value.code == "EXTERNAL_DAEMON_UNAVAILABLE"
    assert not daemon._state_path(tmp_path).exists()


@pytest.mark.parametrize(
    "status,code",
    [
        ({"running": True, "compatible": True, "pid": 999}, "EXTERNAL_DAEMON_PID_MISMATCH"),
        ({"running": True, "compatible": False, "pid": 321}, "DAEMON_VERSION_MISMATCH"),
    ],
)
def test_external_policy_rejects_replacement_and_different_build(
    tmp_path, monkeypatch, status, code
):
    external_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(daemon, "daemon_status", lambda _project: status)
    with pytest.raises(DevError) as caught:
        daemon.ensure_daemon(tmp_path)
    assert caught.value.code == code


def test_external_policy_attaches_only_to_expected_live_owner(tmp_path, monkeypatch):
    external_environment(monkeypatch, tmp_path)
    state = {"pid": 321, "port": 1234, "token": "test-token"}
    monkeypatch.setattr(
        daemon,
        "daemon_status",
        lambda _p: {
            "running": True,
            "compatible": True,
            "pid": 321,
        },
    )
    monkeypatch.setattr(daemon, "_read_state", lambda _p: state)
    assert daemon.ensure_daemon(tmp_path) == state
    # A client in a temporary namespace can attach to a stable external owner.
    monkeypatch.setattr(daemon, "_command_scoped_pid_namespace", lambda: True)
    assert daemon.ensure_daemon(tmp_path) == state


def test_external_policy_owns_stop_and_foreground_start(tmp_path, monkeypatch):
    external_environment(monkeypatch, tmp_path)
    with pytest.raises(DevError) as stop:
        daemon.stop_daemon(tmp_path)
    assert stop.value.code == "EXTERNAL_DAEMON_MANAGED"
    with pytest.raises(DevError) as serve:
        daemon.run_daemon(tmp_path)
    assert serve.value.code == "EXTERNAL_DAEMON_MANAGED"
