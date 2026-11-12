import json

import numpy as np
import pytest
from maa.controller import CustomController

from vibe_maa.backends.maa import MaaBackend
from vibe_maa.errors import DevError
from vibe_maa.reference import show


class ClickChangesFrame(CustomController):
    def __init__(self):
        self.frame = np.zeros((64, 64, 3), dtype=np.uint8)
        self.clicks = []
        super().__init__()

    def connect(self):
        return True

    def request_uuid(self):
        return "offline-reference-example"

    def get_features(self):
        return 0

    def screencap(self):
        return self.frame.copy()

    def click(self, x, y):
        self.clicks.append((x, y))
        self.frame[:] = 255
        return True


def test_published_model_free_example_executes_on_native_maafw(tmp_path):
    pipeline = tmp_path / "resource/pipeline/reference.json"
    pipeline.parent.mkdir(parents=True)
    example = show("pipeline")["examples"]["model_free_click_flow"]
    pipeline.write_text(json.dumps(example))
    controller = ClickChangesFrame()
    backend = MaaBackend(tmp_path, "reference", controller=controller, target_short_side=64)
    try:
        result = backend.run("Start", {}, 5, lambda *_: None)
        assert result["execution_status"] == "succeeded"
        assert controller.clicks == [(20, 20)]
        assert result["nodes"][-1]["name"] == "WhiteVisible"
        assert backend.recognize(controller.frame, example["WhiteVisible"])["hit"] is True
    finally:
        backend.close()


def test_native_conditional_entry_does_not_click_before_matching(tmp_path):
    pipeline = tmp_path / "resource/pipeline/reference.json"
    pipeline.parent.mkdir(parents=True)
    # This entry expects white, but the controller is black until its action runs.
    node = {
        **show("pipeline")["examples"]["model_free_click_flow"]["WhiteVisible"],
        "action": "Click",
        "target": [20, 20],
        "timeout": 100,
        "rate_limit": 20,
    }
    pipeline.write_text(json.dumps({"Entry": node}))
    controller = ClickChangesFrame()
    backend = MaaBackend(tmp_path, "reference", controller=controller, target_short_side=64)
    try:
        assert backend.recognize(controller.frame, node)["hit"] is False
        result = backend.run("Entry", {}, 2, lambda *_: None)
        assert result["execution_status"] == "failed"
        assert controller.clicks == []
    finally:
        backend.close()


def test_invalid_topic_has_actionable_error():
    with pytest.raises(DevError) as caught:
        show("not-a-topic")
    assert caught.value.code == "INVALID_ARGUMENT"


def test_reference_examples_are_independent_between_requests():
    first = show("actions")
    first["examples"]["browser_or_win32_f2"]["key"] = 999
    assert show("actions")["examples"]["browser_or_win32_f2"]["key"] == 113
