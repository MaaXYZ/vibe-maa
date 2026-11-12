import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibe_maa.context import show
from vibe_maa.errors import DevError
from vibe_maa.evidence import save_image, write_json
from vibe_maa.tasks import TaskManager


def test_empty_context_is_read_only_and_bounded(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Context must not start services or construct a store")

    monkeypatch.setattr("vibe_maa.evidence.EvidenceStore.__init__", forbidden)
    monkeypatch.setattr("vibe_maa.daemon.ensure_daemon", forbidden)
    value = show(tmp_path, max_bytes=2000)
    assert value["daemon"] == {"running": False}
    assert value["inventory"]["node_count"] == 0
    assert not (tmp_path / ".maa-dev").exists()
    assert (
        len(
            json.dumps(
                {"schema_version": 1, "ok": True, "result": value}, ensure_ascii=False
            ).encode()
        )
        <= 2000
    )


def test_context_does_not_disclose_interface_configuration_or_external_resources(tmp_path):
    outside = tmp_path.parent / "outside-context-resource"
    outside.mkdir(exist_ok=True)
    (outside / "pipeline").mkdir(exist_ok=True)
    (outside / "pipeline/secret.json").write_text('{"DO_NOT_DISCLOSE":{}}')
    (tmp_path / "interface.json").write_text(
        json.dumps({"api_key": "DO_NOT_DISCLOSE", "resource": [{"path": [str(outside)]}]})
    )
    value = show(tmp_path)
    assert "DO_NOT_DISCLOSE" not in json.dumps(value)
    assert any(w["code"] == "RESOURCE_PATH_OUTSIDE_PROJECT" for w in value["warnings"])


def test_history_ignores_large_corrupt_reports_and_modified_pixels(tmp_path):
    import numpy as np

    runs = tmp_path / ".maa-dev/runs"
    (runs / "run_large").mkdir(parents=True)
    (runs / "run_large/record.json").write_text(" " * (2 * 1024 * 1024 + 1))
    (runs / "run_bad").mkdir()
    (runs / "run_bad/record.json").write_text("{bad")
    obs = tmp_path / ".maa-dev/observations/obs_test"
    digest = save_image(obs / "image.png", np.zeros((10, 10, 3), dtype=np.uint8))
    write_json(obs / "record.json", {"image_path": str(obs / "image.png"), "sha256": digest})
    (obs / "image.png").write_bytes(b"changed")
    value = show(tmp_path)
    assert value["recent_runs"] == []
    assert value["historical_observations"] == []
    codes = {w["code"] for w in value["warnings"]}
    assert {
        "CONTEXT_FILE_TOO_LARGE",
        "INVALID_CONTEXT_FILE",
        "HISTORICAL_IMAGE_UNAVAILABLE_OR_MODIFIED",
    } <= codes


def test_existing_service_is_queried_without_exposing_auth_or_device_url(tmp_path, monkeypatch):
    from vibe_maa import daemon

    path = tmp_path / ".maa-dev/daemon.json"
    write_json(path, {"token": "PRIVATE", "port": 1})
    monkeypatch.setattr(
        daemon, "daemon_status", lambda _: {"running": True, "compatible": True, "token": "PRIVATE"}
    )
    monkeypatch.setattr(daemon, "_read_state", lambda _: {"token": "PRIVATE", "port": 1})
    calls = []

    def exchange(state, method, params, timeout):
        calls.append((method, timeout))
        return {
            "ok": True,
            "result": {
                "sessions": [
                    {
                        "session_id": "session_test",
                        "backend": "browser",
                        "generation": 2,
                        "device": "https://secret:private@example.invalid/?api_key=PRIVATE",
                    }
                ]
            },
        }

    monkeypatch.setattr(daemon, "_exchange", exchange)
    value = show(tmp_path)
    assert calls == [("session.list", 1)]
    assert value["live_sessions"] == [
        {"session_id": "session_test", "backend": "browser", "generation": 2}
    ]
    assert "PRIVATE" not in json.dumps(value)
    assert "vibe-maa observe --session session_test" in value["next_steps"]


def test_busy_service_returns_context_without_retry(tmp_path, monkeypatch):
    from vibe_maa import daemon

    write_json(tmp_path / ".maa-dev/daemon.json", {"token": "private", "port": 1})
    monkeypatch.setattr(daemon, "daemon_status", lambda _: {"running": True, "compatible": True})
    monkeypatch.setattr(daemon, "_read_state", lambda _: {"token": "private", "port": 1})
    calls = []

    def busy(*args):
        calls.append(args)
        raise DevError("DAEMON_UNAVAILABLE", "Busy")

    monkeypatch.setattr(daemon, "_exchange", busy)
    value = show(tmp_path)
    assert len(calls) == 1
    assert value["warnings"] == [{"code": "LIVE_SESSIONS_UNAVAILABLE"}]


class RecordedService:
    def __init__(self, project):
        self.project = project

    def _session(self, session_id):
        return SimpleNamespace(kind="adb", device="test-device", resource_path="resource")

    def run(self, session_id, entry, goal, timeout_seconds):
        path = self.project / ".maa-dev/runs/run_test/record.json"
        result = {
            "run_id": "run_test",
            "session_id": session_id,
            "entry": entry,
            "goal": goal,
            "backend": "adb",
            "device": "test-device",
            "scope": "device",
            "resource_path": "resource",
            "execution_status": "succeeded",
            "goal_status": "passed",
            "inputs_changed": False,
            "inputs": TaskManager(self.project)._resource_snapshot("resource"),
            "report_path": str(path),
        }
        write_json(path, result)
        return result


def test_focused_context_preserves_authoritative_acceptance_and_invalidates_missing_proof(tmp_path):
    pipeline = tmp_path / "resource/pipeline/test.json"
    write_json(
        pipeline, {"Start": {"recognition": "DirectHit"}, "Goal": {"recognition": "ColorMatch"}}
    )
    manager = TaskManager(tmp_path)
    manager.create("demo", "Open an application and verify the goal", "Start", "Goal")
    attempt = manager.run(RecordedService(tmp_path), "session_test", "demo")
    assert attempt["context"]["state"] == "accepted"
    value = show(tmp_path, task="demo", max_bytes=2000)
    assert value["task"]["state"] == "accepted"
    assert value["task"]["scope"] == "device"
    assert value["task"]["acceptance_scope"] == "configured_checks_only"
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= 1872
    Path(attempt["attempt"]["report_path"]).unlink()
    assert show(tmp_path, task="demo")["task"]["state"] == "needs_validation"


def test_large_inventory_is_explicitly_truncated(tmp_path):
    write_json(
        tmp_path / "resource/pipeline/nodes.json",
        {f"Node_{i}": {"action": "DoNothing"} for i in range(200)},
    )
    for i in range(15):
        TaskManager(tmp_path).create(f"task_{i}", "目标" * 200, "Start", "Goal")
    value = show(tmp_path, max_bytes=2000)
    assert value["budget"]["truncated"] is True
    assert value["budget"]["omitted"]
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= 1872


@pytest.mark.parametrize("budget", [1999, 64001, True, 2000.0])
def test_invalid_budget_is_structured(tmp_path, budget):
    with pytest.raises(DevError) as caught:
        show(tmp_path, max_bytes=budget)
    assert caught.value.code == "INVALID_ARGUMENT"


def test_unsafe_task_name_is_rejected_without_creating_state(tmp_path):
    with pytest.raises(DevError):
        show(tmp_path, task="../elsewhere")
    assert not (tmp_path / ".maa-dev").exists()


def test_multibyte_focused_task_retains_verdict_with_small_budget(tmp_path):
    name = "task_" + "x" * 75
    TaskManager(tmp_path).create(name, "目标" * 3000, "入口" * 100, "完成" * 100)
    value = show(tmp_path, task=name, max_bytes=2000)
    assert value["task"]["state"] == "draft"
    assert value["task"]["scope"] == "unknown"
    assert value["task"]["acceptance_scope"] == "configured_checks_only"
    assert value["task"]["missing_scenarios_count"] == 1
    assert value["budget"]["truncated"]
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= 1872


def test_malformed_history_and_live_response_do_not_break_snapshot(tmp_path, monkeypatch):
    from vibe_maa import daemon

    write_json(tmp_path / ".maa-dev/observations/obs_bad/record.json", {"image_path": {}})
    write_json(tmp_path / ".maa-dev/daemon.json", {"token": "private", "port": 1})
    monkeypatch.setattr(daemon, "daemon_status", lambda _: {"running": True, "compatible": True})
    monkeypatch.setattr(daemon, "_read_state", lambda _: {"token": "private", "port": 1})
    monkeypatch.setattr(daemon, "_exchange", lambda *args: {"ok": True, "result": {"sessions": {}}})
    value = show(tmp_path)
    assert not value.get("live_sessions")
    assert value["historical_observations"] == []
    assert {warning["code"] for warning in value["warnings"]} == {
        "LIVE_SESSIONS_UNAVAILABLE",
        "HISTORICAL_IMAGE_UNAVAILABLE_OR_MODIFIED",
    }
