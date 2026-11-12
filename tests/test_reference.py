import json
import zipfile

import numpy as np
import pytest
from maa.controller import CustomController

from vibe_maa.backends.maa import MaaBackend
from vibe_maa.cli import initialize
from vibe_maa.errors import DevError
from vibe_maa.reference import show
from vibe_maa.reports import Reports


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


def test_project_reference_handoff_matches_init_and_exports_with_its_entry(tmp_path):
    initialize(tmp_path)
    initialized = json.loads((tmp_path / "interface.json").read_text())
    reference = show("project")
    interface = reference["examples"]["interface.json"]
    for field in ("interface_version", "controller", "resource"):
        assert interface[field] == initialized[field]
    public_task = interface["task"][0]
    assert public_task["name"] != public_task["entry"]
    assert set(public_task) == {"name", "label", "entry"}
    # Exercise the ordinary handoff/export, without any target application's solution.
    (tmp_path / "interface.json").write_text(json.dumps(interface))
    nodes = {public_task["entry"]: {"recognition": "DirectHit", "action": "DoNothing"}}
    (tmp_path / "resource/pipeline/task.json").write_text(json.dumps(nodes))
    Reports(tmp_path).export_project("project.zip")
    with zipfile.ZipFile(tmp_path / "project.zip") as archive:
        assert json.loads(archive.read("interface.json")) == interface
        exported_nodes = json.loads(archive.read("resource/pipeline/task.json"))
        assert interface["task"][0]["entry"] in exported_nodes
        assert json.loads(archive.read("manifest.json"))["kind"] == "maa_project"
    assert "not new ProjectInterface" in reference["content"]
    assert "browser controller integration" in reference["content"]


def test_project_reference_cli_is_bounded_and_does_not_create_a_project(
    tmp_path, capsys, monkeypatch
):
    from vibe_maa import cli

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline project reference must not connect to a service")

    monkeypatch.setattr(cli, "request", forbidden)
    monkeypatch.setattr(cli, "daemon_status", forbidden)
    project = tmp_path / "uncreated"
    assert cli.main(["--project", str(project), "reference", "--topic", "project"]) == 0
    output = capsys.readouterr().out
    response = json.loads(output)
    assert response["ok"] is True
    assert response["result"]["topic"] == "project"
    assert response["result"]["protocol"] == "MaaFramework ProjectInterface V2"
    assert len(output.encode()) < 5000
    assert not project.exists()
