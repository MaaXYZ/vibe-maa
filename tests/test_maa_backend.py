"""Exercise the installed native MaaFW runtime, not a fake task executor."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from maa.controller import CustomController

from vibe_maa.backends.maa import MaaBackend
from vibe_maa.errors import DevError


class FrameController(CustomController):
    def __init__(self):
        self.frame = np.zeros((64, 64, 3), dtype=np.uint8)
        self.clicks = 0
        self.captures = 0
        super().__init__()

    def connect(self):
        return True

    def request_uuid(self):
        return "vibe-maa-native-test"

    def get_features(self):
        return 0

    def start_app(self, intent):
        return True

    def stop_app(self, intent):
        return True

    def screencap(self):
        self.captures += 1
        return self.frame.copy()

    def click(self, x, y):
        self.clicks += 1
        self.frame[:] = 255
        return True

    def swipe(self, x1, y1, x2, y2, duration):
        return True

    def touch_down(self, contact, x, y, pressure):
        return True

    def touch_move(self, contact, x, y, pressure):
        return True

    def touch_up(self, contact):
        return True

    def click_key(self, keycode):
        return True

    def input_text(self, text):
        return True

    def key_down(self, keycode):
        return True

    def key_up(self, keycode):
        return True


@pytest.fixture
def native(tmp_path):
    resource = tmp_path / "resource" / "pipeline"
    resource.mkdir(parents=True)
    (resource / "test.json").write_text('{"Placeholder": {}}')
    controller = FrameController()
    backend = MaaBackend(tmp_path, "test", controller=controller, target_short_side=64)
    yield backend, controller, resource
    backend.close()


def color_node(color=0, **extra):
    return {
        "recognition": "ColorMatch",
        "lower": [color, color, color],
        "upper": [color, color, color],
        "count": 1,
        "action": "DoNothing",
        "rate_limit": 10,
        "timeout": 3000,
        **extra,
    }


def write_pipeline(path: Path, nodes):
    (path / "test.json").write_text(json.dumps(nodes), encoding="utf-8")


def test_probe_uses_supplied_pixels_and_never_runs_actions(native):
    backend, controller, _ = native
    black = np.zeros((64, 64, 3), dtype=np.uint8)
    white = np.full_like(black, 255)
    node = color_node(action="Click", target=[10, 10, 1, 1], next=["MissingNode"])
    assert backend.recognize(black, node)["hit"] is True
    assert backend.recognize(white, node)["hit"] is False
    assert controller.clicks == 0
    assert controller.captures == 0


def test_probe_inverse_and_disabled_are_explicit(native):
    backend, _, _ = native
    image = np.zeros((64, 64, 3), dtype=np.uint8)
    result = backend.recognize(image, color_node(inverse=True))
    assert result["hit"] is False
    assert result["recognition_hit"] is True
    with pytest.raises(DevError, match="Disabled"):
        backend.recognize(image, color_node(enabled=False))


def test_run_executes_native_pipeline_and_records_original_frames(native):
    backend, controller, resource = native
    write_pipeline(
        resource,
        {
            "Start": {"action": "Click", "target": [10, 10, 1, 1], "next": ["White"]},
            "White": color_node(255),
        },
    )
    recorded = []
    result = backend.run("Start", {}, 5, lambda event, image: recorded.append((event, image)))
    assert result["execution_status"] == "succeeded"
    assert controller.clicks == 1
    assert [n["name"] for n in result["nodes"]] == ["Start", "White"]
    reco_events = [
        (e, im)
        for e, im in recorded
        if e["message"] == "Node.Recognition.Succeeded" and e["details"]["name"] == "White"
    ]
    assert reco_events
    for event, image in reco_events:
        assert event["details"]["reco_id"] == event["recognition"]["reco_id"]
        assert image is not None
        assert np.all(image == 255)


def test_timeout_stops_and_keeps_failed_recognition_evidence(native):
    backend, _, resource = native
    write_pipeline(
        resource,
        {"Start": {"next": ["White"], "pre_delay": 0, "post_delay": 0}, "White": color_node(255)},
    )
    recorded = []
    result = backend.run("Start", {}, 0.6, lambda e, image: recorded.append((e, image)))
    assert result["execution_status"] == "timeout"
    assert not backend.tasker.running
    failures = [(e, image) for e, image in recorded if e["message"] == "Node.Recognition.Failed"]
    assert failures
    for event, image in failures:
        assert event["recognition"]["hit"] is False
        assert image is not None and np.all(image == 0)


def test_new_run_reloads_files_without_reconnecting(native):
    backend, controller, resource = native
    write_pipeline(resource, {"Entry": {"action": "DoNothing"}})
    assert backend.run("Entry", {}, 5, lambda *_: None)["execution_status"] == "succeeded"
    assert controller.clicks == 0
    write_pipeline(resource, {"Entry": {"action": "Click", "target": [2, 2, 1, 1]}})
    assert backend.run("Entry", {}, 5, lambda *_: None)["execution_status"] == "succeeded"
    assert controller.clicks == 1


def test_ocr_missing_model_is_not_reported_as_empty_text(native):
    backend, _, _ = native
    with pytest.raises(DevError) as error:
        backend.ocr(np.zeros((64, 64, 3), dtype=np.uint8))
    assert error.value.code == "OCR_UNAVAILABLE"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_invalid_budget_rejected_before_execution(native, timeout):
    backend, controller, _ = native
    with pytest.raises(DevError):
        backend.run("Start", {}, timeout, lambda *_: None)
    assert controller.clicks == 0


def test_recorder_failure_stops_native_task(native):
    backend, _, resource = native
    write_pipeline(resource, {"Start": {"next": ["White"]}, "White": color_node(255)})

    def broken_recorder(*_):
        raise OSError("cannot persist evidence")

    with pytest.raises(OSError, match="cannot persist"):
        backend.run("Start", {}, 5, broken_recorder)
    assert not backend.tasker.running


def test_probe_honors_native_default_pipeline_semantics(native):
    backend, _, resource = native
    defaults = resource.parent / "default_pipeline.json"
    defaults.write_text(json.dumps({"Default": {"inverse": True}}))
    result = backend.recognize(np.zeros((64, 64, 3), dtype=np.uint8), color_node())
    assert result["recognition_hit"] is True
    assert result["hit"] is False
    defaults.write_text(json.dumps({"Default": {"enabled": False}}))
    with pytest.raises(DevError, match="Disabled"):
        backend.recognize(np.zeros((64, 64, 3), dtype=np.uint8), color_node())
