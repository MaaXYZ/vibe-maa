"""Native execution success alone must not satisfy a real application task."""

import os
from pathlib import Path

import pytest

from vibe_maa.cli import initialize


@pytest.mark.browser
@pytest.mark.parametrize("target", ["jupyterlab", "pdfjs"])
def test_application_oracle_rejects_successful_noop(tmp_path, monkeypatch, target):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    from grader import grade_target

    initialize(tmp_path)
    pipeline = tmp_path / "resource/pipeline/noop.json"
    pipeline.write_text('{"AgentTask":{"action":"DoNothing"}}', encoding="utf-8")
    cache = Path(os.environ.get("APP_CACHE", ".cache/targets"))
    result, _ = grade_target(tmp_path, target, cache)

    assert result["blocked_external_requests"] == []
    assert result["passed"] is False
    assert len(result["checks"]) == 2
    for check in result["checks"]:
        assert check.get("execution_status") == "succeeded", check
        assert check["after"] == check["before"]
        assert check["application_goal_met"] is False
        assert check["passed"] is False
