import shutil
from pathlib import Path

import numpy as np
import pytest

from vibe_maa.backends.demo import DemoBackend, DemoWorld
from vibe_maa.errors import DevError

EXAMPLE = Path(__file__).parents[1] / "examples" / "mailbox" / "resource"


@pytest.fixture
def project(tmp_path):
    shutil.copytree(EXAMPLE, tmp_path / "resource")
    return tmp_path


def test_historical_ocr_uses_saved_pixels():
    world = DemoWorld()
    home = world.capture()
    world.click(300, 170)
    assert world.state == "mailbox"
    assert [e["text"] for e in world.ocr(home)] == ["Home", "Mailbox"]
    assert world.ocr(world.capture())[-1]["text"] == "No claimable mail"


def test_demo_native_capture_keeps_coordinate_space(project):
    backend = DemoBackend(project)
    try:
        image = backend.capture()
        assert image.shape == (360, 640, 3)
        assert np.array_equal(image[0, 0], [20, 20, 20])
        assert backend.act({"type": "click", "x": 300, "y": 170})
        assert backend.world.state == "mailbox"
    finally:
        backend.close()


def test_native_probe_never_executes_action_or_next(project):
    backend = DemoBackend(project)
    try:
        image = backend.capture()
        result = backend.recognize(
            image,
            {
                "recognition": "ColorMatch",
                "roi": [220, 140, 200, 60],
                "lower": [200, 200, 200],
                "upper": [200, 200, 200],
                "count": 100,
                "action": "Click",
                "next": ["MailboxVisible"],
            },
        )
        assert result["hit"] is True
        assert backend.world.actions == []
        assert backend.world.state == "home"
    finally:
        backend.close()


@pytest.mark.parametrize(
    "action",
    [
        {"type": "text", "text": "hello"},
        {"type": "swipe", "x1": 10, "y1": 10, "x2": 100, "y2": 100, "duration": 100},
        {"type": "key", "key": 999},
    ],
)
def test_unsupported_demo_inputs_fail_explicitly(project, action):
    backend = DemoBackend(project)
    try:
        with pytest.raises(DevError, match="Controller operation failed"):
            backend.act(action)
        assert backend.world.state == "home"
    finally:
        backend.close()


def test_demo_back_key_is_supported(project):
    backend = DemoBackend(project, initial_state="mailbox")
    try:
        assert backend.act({"type": "key", "key": 4})
        assert backend.world.state == "home"
    finally:
        backend.close()


@pytest.mark.parametrize("initial_state", ["home", "mailbox", "popup"])
def test_standard_pipeline_runs_on_real_tasker(project, initial_state):
    backend = DemoBackend(project, initial_state=initial_state)
    events = []
    try:
        result = backend.run(
            "OpenMailbox", {}, 5, lambda event, image: events.append((event, image))
        )
        assert result["execution_status"] == "succeeded"
        assert result["nodes"][-1]["name"] == "HomeVisible"
        assert backend.world.state == "home"
        assert any(event["message"] == "Node.Recognition.Succeeded" for event, _ in events)
    finally:
        backend.close()


def test_missing_popup_branch_has_original_failure_frame_then_repairs(project):
    backend = DemoBackend(project, initial_state="popup")
    events = []
    try:
        broken = {"OpenMailbox": {"next": ["ClickMailbox", "MailboxVisible"]}}
        result = backend.run(
            "OpenMailbox", broken, 3, lambda event, image: events.append((event, image))
        )
        assert result["execution_status"] == "failed"
        assert backend.world.state == "popup"
        failed_images = [
            image
            for event, image in events
            if event["message"] == "Node.Recognition.Failed" and image is not None
        ]
        assert failed_images
        assert np.array_equal(failed_images[-1][0, 0], [60, 60, 60])
        fixed = backend.run("OpenMailbox", {}, 5, lambda *_: None)
        assert fixed["execution_status"] == "succeeded"
        assert backend.world.state == "home"
    finally:
        backend.close()
