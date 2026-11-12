import json
from pathlib import Path

import numpy as np
import pytest

from vibe_maa.errors import DevError
from vibe_maa.service import DevelopmentService, Session


class Device:
    def __init__(self):
        self.image = np.zeros((80, 100, 3), dtype=np.uint8)
        self.inputs = []
        self.failure = False

    def capture(self):
        return self.image.copy()

    def ocr(self, image):
        return [{"text": "Open", "box": [10, 10, 30, 20], "score": 1.0}]

    def act(self, action):
        self.inputs.append(action)
        if self.failure:
            raise RuntimeError("Disconnected after dispatch")
        self.image[10:30, 10:40] = 200
        return True

    def recognize(self, image, node):
        return {"hit": bool(image[0, 0, 0]), "all_results": [{"text": "candidate"}]}

    def run(self, entry, override, timeout_seconds, on_event):
        on_event({"message": "Node.Recognition.Failed", "reco_id": 7}, self.image)
        return {"execution_status": "succeeded"}

    def close(self):
        pass


@pytest.fixture
def service(tmp_path):
    pipeline = tmp_path / "resource" / "pipeline"
    pipeline.mkdir(parents=True)
    (pipeline / "test.json").write_text(
        json.dumps(
            {
                "Start": {"recognition": "DirectHit"},
                "Goal": {"recognition": "ColorMatch", "lower": [1, 1, 1], "upper": [255, 255, 255]},
                "Always": {"recognition": {"type": "DirectHit"}},
            }
        )
    )
    engine = DevelopmentService(tmp_path)
    device = Device()
    engine.sessions["s1"] = Session("s1", device, "fake", "test", "resource")
    yield engine, device
    engine.close()


def test_action_records_before_after_and_rejects_reuse(service):
    engine, device = service
    obs = engine.observe("s1")
    action = engine.act("s1", obs["observation_id"], target="e1")
    assert device.inputs == [{"type": "click", "x": 25, "y": 20}]
    assert action["status"] == "succeeded"
    assert action["observation"]["generation"] == 1
    assert (
        engine.store.read("observations", action["before_observation"])["sha256"] == obs["sha256"]
    )
    assert (
        engine.store.read("actions", action["action_id"])["after_observation"]
        == action["observation"]["observation_id"]
    )
    with pytest.raises(DevError, match="Observe again"):
        engine.act("s1", obs["observation_id"], target="e1")
    assert len(device.inputs) == 1


def test_external_target_change_does_not_dispatch(service):
    engine, device = service
    obs = engine.observe("s1")
    device.image[10:30, 10:40] = 255
    with pytest.raises(DevError) as caught:
        engine.act("s1", obs["observation_id"], target="e1")
    assert caught.value.code == "STALE_OBSERVATION"
    assert caught.value.details["observation"]["observation_id"] != obs["observation_id"]
    assert device.inputs == []


def test_animation_outside_target_does_not_prevent_click(service):
    engine, device = service
    obs = engine.observe("s1")
    device.image[50:] = 255
    assert engine.act("s1", obs["observation_id"], target="e1")["status"] == "succeeded"


def test_action_exception_leaves_durable_intent(service):
    engine, device = service
    device.failure = True
    obs = engine.observe("s1")
    result = engine.act("s1", obs["observation_id"], target="e1")
    assert result["status"] == "failed"
    intent = engine.store.directory("actions", result["action_id"]) / "intent.json"
    assert json.loads(intent.read_text())["status"] == "pending"
    assert engine.sessions["s1"].generation == 1
    reopened = DevelopmentService(engine.project)
    assert reopened.artifact_read("actions", result["action_id"])["status"] == "failed"


def test_probe_accepts_historical_session_but_action_does_not(service):
    engine, device = service
    obs = engine.observe("s1")
    engine.sessions["s2"] = Session("s2", device, "fake", "test", "resource")
    assert engine.probe("s2", obs["observation_id"], "Goal")["result"]["hit"] is False
    with pytest.raises(DevError) as caught:
        engine.act("s2", obs["observation_id"], target="e1")
    assert caught.value.code == "OBSERVATION_SESSION_MISMATCH"
    assert device.inputs == []


def test_modified_saved_pixels_are_rejected(service):
    engine, _ = service
    obs = engine.observe("s1")
    Path(obs["image_path"]).write_bytes(b"changed")
    with pytest.raises(DevError) as caught:
        engine.probe("s1", obs["observation_id"], "Goal")
    assert caught.value.code == "EVIDENCE_MODIFIED"


def test_task_success_does_not_imply_goal_success(service):
    engine, _ = service
    result = engine.run("s1", "Start", goal="Goal")
    assert result["execution_status"] == "succeeded"
    assert result["goal_status"] == "failed"
    assert result["goal_check"]["all_results"]  # Candidate presence is not the hit flag.
    assert Path(result["events"][0]["image_path"]).is_file()
    assert engine.run("s1", "Start")["goal_status"] == "unknown"


@pytest.mark.parametrize("goal", ["Start", "Always"])
def test_directhit_is_not_an_observable_goal(service, goal):
    engine, _ = service
    with pytest.raises(DevError) as caught:
        engine.run("s1", "Start", goal=goal)
    assert caught.value.code == "INVALID_GOAL"


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), 0, -1, True, 301])
def test_invalid_budget_does_not_run(service, budget):
    engine, _ = service
    with pytest.raises(DevError):
        engine.run("s1", "Start", timeout_seconds=budget)
    assert engine.sessions["s1"].generation == 0


def test_assets_keep_crop_provenance_and_cannot_overwrite(service):
    engine, _ = service
    obs = engine.observe("s1")
    asset = engine.asset_extract(obs["observation_id"], [10, 10, 30, 20], "open")
    assert asset["source_sha256"] == obs["sha256"]
    assert Path(asset["image_path"]).is_file()
    with pytest.raises(DevError):
        engine.asset_extract(obs["observation_id"], [10, 10, 30, 20], "open")
    with pytest.raises(DevError):
        engine.asset_extract(obs["observation_id"], [10, 10, 30, 20], "../escape")


def test_cases_use_explicit_expected_hit(service):
    engine, _ = service
    obs = engine.observe("s1")
    cases = engine.project / "cases.json"
    cases.write_text(
        json.dumps([{"node": "Goal", "image": obs["image_path"], "expected_hit": False}])
    )
    result = engine.case_check("s1", "cases.json")
    assert result["passed"] is True
    assert result["scope"] == "offline_recognition_only"


def test_device_lease_excludes_other_projects_and_releases(service, monkeypatch, tmp_path):
    from vibe_maa.backends import maa

    engine, device = service
    monkeypatch.setattr(maa, "MaaBackend", lambda *args, **kwargs: device)
    opened = engine.session_open(backend="adb", device="localhost:19731")
    other = DevelopmentService(tmp_path / "another-project")
    try:
        with pytest.raises(DevError) as caught:
            other.session_open(backend="adb", device="127.0.0.1:19731")
        assert caught.value.code == "DEVICE_BUSY"
        engine.session_close(opened["session_id"])
        assert other.session_open(backend="adb", device="127.0.0.1:19731")["session_id"]
    finally:
        other.close()


def test_key_and_text_require_a_visual_guard(service):
    engine, device = service
    observation = engine.observe("s1")
    with pytest.raises(DevError, match="guard box"):
        engine.act("s1", observation["observation_id"], type="text", text="hello")
    assert not device.inputs


def test_run_keeps_source_bytes_and_final_image_after_budget(service, monkeypatch):
    engine, device = service

    def run(entry, override, timeout_seconds, on_event):
        for _ in range(260):
            on_event({"message": "Node.Recognition.Failed"}, device.image)
        last = np.full_like(device.image, 255)
        on_event({"message": "Node.Recognition.Failed", "reco_id": 261}, last)
        return {"execution_status": "failed"}

    monkeypatch.setattr(device, "run", run)
    result = engine.run("s1", "Start")
    assert result["truncated_images"] == 5
    assert result["last_recognition"]["reco_id"] == 261
    from vibe_maa.evidence import read_image

    assert np.all(read_image(Path(result["last_recognition"]["image_path"])) == 255)
    directory = Path(result["report_path"]).parent
    assert (directory / "input-files/resource/pipeline/test.json").read_bytes() == (
        engine.project / "resource/pipeline/test.json"
    ).read_bytes()
    assert result["maafw_version"]
