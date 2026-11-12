"""Actual renderer regression: draw.io's canonical rectangle is styled as label."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.browser
DIRECTORY = Path(__file__).parent
sys.path.insert(0, str(DIRECTORY))
spec = importlib.util.spec_from_file_location("agent_grader_regression", DIRECTORY / "grader.py")
grader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grader)


@pytest.mark.parametrize(
    "palette_x,expected", [(36, True), (188, False)], ids=["rectangle-label-renderer", "ellipse"]
)
def test_real_shape_renderer_acceptance(tmp_path, palette_x, expected):
    from vibe_maa.cli import initialize

    initialize(tmp_path)
    nodes = {
        "AgentTask": {
            "action": "Click",
            "target": [palette_x, 180, 1, 1],
            "post_delay": 250,
            "next": ["Select"],
        },
        "Select": {
            "action": "Click",
            "target": [630, 420, 1, 1],
            "post_delay": 100,
            "next": ["Edit"],
        },
        "Edit": {"action": "ClickKey", "key": 113, "post_delay": 100, "next": ["Label"]},
        "Label": {
            "action": "InputText",
            "input_text": "Vibe Maa",
            "post_delay": 100,
            "next": ["Finish"],
        },
        "Finish": {"action": "ClickKey", "key": 27, "post_delay": 100},
    }
    (tmp_path / "resource/pipeline/task.json").write_text(json.dumps(nodes), encoding="utf-8")
    cache = Path(os.environ.get("VIBE_MAA_DRAWIO_CACHE", "work/targets"))
    with grader.drawio_server(cache) as (url, _upstream):
        result = grader.grade_drawio(tmp_path, url, os.environ.get("VIBE_MAA_BROWSER_EXECUTABLE"))
    assert result["passed"] is expected
    for check in result["checks"]:
        assert check["execution_status"] == "succeeded"
        assert len(check["document"]) == 1 and check["document"][0]["label"] == "Vibe Maa"
        assert check["document"][0]["rectangle_renderer"] is expected
        assert check["document"][0]["ellipse_renderer"] is not expected
    assert result["blocked_external_requests"] == []
