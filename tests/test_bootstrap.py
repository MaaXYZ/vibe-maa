import json
from pathlib import Path

import pytest

from vibe_maa.backends.demo import DemoBackend
from vibe_maa.bootstrap import initialize_demo, skill_export, skill_install, skill_show
from vibe_maa.errors import DevError
from vibe_maa.service import DevelopmentService


def test_initialized_demo_is_complete_and_native_runnable(tmp_path):
    result = initialize_demo(tmp_path)
    assert result["scope"] == "fixture"
    interface = json.loads((tmp_path / "interface.json").read_text())
    assert interface["task"][0]["entry"] == "OpenMailbox"
    service = DevelopmentService(tmp_path)
    try:
        sid = service.session_open(backend="demo", initial_state="popup")["session_id"]
        report = service.run(sid, result["entry"], goal=result["goal"])
        assert report["execution_status"] == "succeeded"
        assert report["goal_status"] == "passed"
        assert service.case_check(sid, result["cases"])["passed"] is True
    finally:
        service.close()


def test_starter_requires_actual_source_edit(tmp_path):
    result = initialize_demo(tmp_path, starter=True)
    path = tmp_path / "resource/pipeline/mailbox.json"
    nodes = json.loads(path.read_text())
    assert "DismissAnnouncement" not in nodes
    assert "DismissAnnouncement" not in nodes["OpenMailbox"]["next"]
    backend = DemoBackend(tmp_path, initial_state="popup")
    try:
        report = backend.run(result["entry"], {}, 3, lambda *_: None)
        assert report["execution_status"] == "failed"
        assert backend.world.state == "popup"
        nodes["DismissAnnouncement"] = {
            "recognition": "ColorMatch",
            "roi": [220, 210, 200, 60],
            "lower": [220, 220, 220],
            "upper": [220, 220, 220],
            "count": 100,
            "action": "Click",
            "next": ["ClickMailbox"],
            "timeout": 1000,
        }
        nodes["OpenMailbox"]["next"].insert(0, "DismissAnnouncement")
        path.write_text(json.dumps(nodes))
        assert (
            backend.run(result["entry"], {}, 3, lambda *_: None)["execution_status"] == "succeeded"
        )
    finally:
        backend.close()


def test_conflicting_file_prevents_partial_initialization(tmp_path):
    target = tmp_path / "resource/pipeline/mailbox.json"
    target.parent.mkdir(parents=True)
    target.write_text("do not touch")
    with pytest.raises(DevError) as caught:
        initialize_demo(tmp_path)
    assert caught.value.code == "ALREADY_EXISTS"
    assert target.read_text() == "do not touch"
    assert not (tmp_path / "interface.json").exists()


def test_existing_gitignore_entries_are_preserved(tmp_path):
    (tmp_path / ".gitignore").write_text("private.txt\n")
    initialize_demo(tmp_path)
    assert (tmp_path / ".gitignore").read_text() == "private.txt\n.maa-dev/\n"


def test_packaged_skill_matches_canonical_source():
    source = Path(__file__).parents[1] / "skills/vibe-maa/SKILL.md"
    assert skill_show()["content"] == source.read_text()


@pytest.mark.parametrize("target,folder", [("codex", ".agents"), ("claude", ".claude")])
def test_project_skill_installation_and_no_overwrite(tmp_path, target, folder):
    result = skill_install(tmp_path, target)
    path = tmp_path / folder / "skills/vibe-maa/SKILL.md"
    assert result["scope"] == "project"
    assert Path(result["skill_path"]) == path
    assert path.read_text() == skill_show()["content"]
    path.write_text("custom edits")
    with pytest.raises(DevError) as caught:
        skill_install(tmp_path, target)
    assert caught.value.code == "ALREADY_EXISTS"
    assert path.read_text() == "custom edits"


def test_skill_export_rejects_path_escape_and_unknown_target(tmp_path):
    with pytest.raises(DevError) as caught:
        skill_export(tmp_path, "../outside")
    assert caught.value.code == "INVALID_PATH"
    with pytest.raises(DevError):
        skill_install(tmp_path, "unsupported")
