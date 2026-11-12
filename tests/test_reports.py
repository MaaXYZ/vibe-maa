import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from vibe_maa.errors import DevError
from vibe_maa.evidence import EvidenceStore, save_image, write_json
from vibe_maa.reports import Reports


@pytest.fixture
def recorded_project(tmp_path):
    resource = tmp_path / "resource" / "pipeline"
    resource.mkdir(parents=True)
    pipeline = resource / "mail.json"
    pipeline.write_text('{"Open": {"recognition": "DirectHit"}}\n')
    write_json(
        tmp_path / "interface.json",
        {
            "interface_version": 2,
            "name": "test",
            "resource": [{"name": "Default", "path": ["./resource"]}],
            "controller": [{"name": "Android", "type": "Adb"}],
            "task": [],
        },
    )
    store = EvidenceStore(tmp_path)
    observations = []
    for oid, value in [("obs_before", 20), ("obs_after", 60)]:
        path = store.directory("observations", oid) / "image.png"
        digest = save_image(path, np.full((8, 12, 3), value, dtype=np.uint8))
        record = {"observation_id": oid, "image_path": str(path), "sha256": digest}
        store.write("observations", oid, record)
        observations.append(record)
    run_id = "run_one"
    directory = store.directory("runs", run_id)
    image = directory / "frame-00001.png"
    digest = save_image(image, np.full((8, 12, 3), 60, dtype=np.uint8))
    event = {
        "sequence": 1,
        "message": "Node.Recognition.Failed",
        "image_path": str(image),
        "sha256": digest,
        "details": {"name": "Mailbox"},
    }
    (directory / "events.jsonl").write_text(json.dumps(event) + "\n")
    copied = directory / "input-files" / "resource" / "pipeline" / "mail.json"
    copied.parent.mkdir(parents=True)
    copied.write_bytes(pipeline.read_bytes())
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "entry": "Open",
        "started_at": "2026-11-12T00:00:00Z",
        "backend": "demo",
        "device": "demo",
        "scope": "fixture",
        "execution_status": "failed",
        "goal_status": "failed",
        "before_observation": "obs_before",
        "after_observation": "obs_after",
        "report_path": str(directory / "record.json"),
        "last_recognition": event,
        "events": [
            event,
            {"message": "Node.NextList.Failed", "details": {"list": [{"name": "Mailbox"}]}},
        ],
        "nodes": [{"name": "Open", "completed": True}],
        "inputs": {
            "resource/pipeline/mail.json": hashlib.sha256(pipeline.read_bytes()).hexdigest()
        },
    }
    store.write("runs", run_id, report)
    write_json(store.root / "daemon.json", {"token": "SECRET-TOKEN"})
    logs = store.root / "native-logs"
    logs.mkdir()
    (logs / "private.log").write_text("PRIVATE-LOG")
    return tmp_path, store, report


def test_summary_lists_facts_and_preserves_fixture_scope(recorded_project):
    project, _, _ = recorded_project
    reports = Reports(project)
    assert reports.list_runs()["runs"][0]["scope"] == "fixture"
    summary = reports.summary("run_one")
    assert summary["execution_status"] == "failed"
    assert summary["last_node"]["name"] == "Open"
    assert summary["waiting_for"] == ["Mailbox"]
    assert summary["interpretation"] == "Recorded facts only; no root cause was inferred."


def test_run_archive_is_portable_verified_and_excludes_runtime_credentials(
    recorded_project, tmp_path
):
    project, _, report = recorded_project
    result = Reports(project).export_run("run_one", "out/evidence.zip")
    extraction = tmp_path / "relocated"
    with zipfile.ZipFile(result["output"]) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        assert all("daemon.json" not in name and "native-logs" not in name for name in names)
        for item in result["manifest"]["files"]:
            assert hashlib.sha256(archive.read(item["path"])).hexdigest() == item["sha256"]
        archive.extractall(extraction)
    manifest = json.loads((extraction / "manifest.json").read_text())
    exported = json.loads((extraction / manifest["report"]).read_text())
    assert (extraction / exported["report_path"]).is_file()
    assert (extraction / exported["last_recognition"]["image_path"]).is_file()
    assert str(project) not in json.dumps(exported)
    for oid in [report["before_observation"], report["after_observation"]]:
        observation = json.loads(
            (extraction / f"evidence/observations/{oid}/record.json").read_text()
        )
        assert (extraction / observation["image_path"]).is_file()
    source = project / "resource/pipeline/mail.json"
    saved = extraction / "evidence/runs/run_one/input-files/resource/pipeline/mail.json"
    assert saved.read_bytes() == source.read_bytes()


def test_run_export_rejects_modified_original_frame(recorded_project):
    project, _, report = recorded_project
    Path(report["last_recognition"]["image_path"]).write_bytes(b"modified")
    with pytest.raises(DevError) as caught:
        Reports(project).export_run("run_one", "out.zip")
    assert caught.value.code == "EVIDENCE_MODIFIED"
    assert not (project / "out.zip").exists()


def test_run_export_rejects_external_image_reference(recorded_project, tmp_path):
    project, store, report = recorded_project
    outside = project.parent / "outside-frame.png"
    save_image(outside, np.zeros((8, 12, 3), dtype=np.uint8))
    report["last_recognition"]["image_path"] = str(outside)
    store.write("runs", "run_one", report)
    with pytest.raises(DevError) as caught:
        Reports(project).export_run("run_one", "out.zip")
    assert caught.value.code == "EXPORT_PATH_ESCAPE"


def test_run_export_rejects_missing_referenced_observation(recorded_project):
    project, store, _ = recorded_project
    (store.directory("observations", "obs_after") / "image.png").unlink()
    with pytest.raises(DevError) as caught:
        Reports(project).export_run("run_one", "out.zip")
    assert caught.value.code == "EVIDENCE_MISSING"


def test_run_archive_never_overwrites(recorded_project):
    project, _, _ = recorded_project
    Reports(project).export_run("run_one", "out.zip")
    original = (project / "out.zip").read_bytes()
    with pytest.raises(DevError) as caught:
        Reports(project).export_run("run_one", "out.zip")
    assert caught.value.code == "ALREADY_EXISTS"
    assert (project / "out.zip").read_bytes() == original


def test_project_archive_contains_runtime_resources_and_portable_cases(recorded_project):
    project, _, _ = recorded_project
    cases = project / "dev/cases"
    cases.mkdir(parents=True)
    save_image(cases / "home.png", np.zeros((8, 12, 3), dtype=np.uint8))
    (cases / "mail.jsonc").write_text('[{node:"Open",image:"home.png",expected_hit:true,},]')
    (project / ".env").write_text("DO-NOT-EXPORT")
    result = Reports(project).export_project("delivery.zip")
    with zipfile.ZipFile(result["output"]) as archive:
        assert "interface.json" in archive.namelist()
        assert "resource/pipeline/mail.json" in archive.namelist()
        assert "dev/cases/home.png" in archive.namelist()
        assert ".env" not in archive.namelist()
        assert not any(".maa-dev" in name for name in archive.namelist())
        exported_case = json.loads(archive.read("dev/cases/mail.jsonc"))[0]
        assert exported_case["image"] == "home.png"
        assert (
            result["manifest"]["verification"]
            == "Packaging alone does not establish task acceptance."
        )


def test_project_manifest_is_deterministic(recorded_project):
    project, _, _ = recorded_project
    reports = Reports(project)
    first = reports.export_project("first.zip")
    second = reports.export_project("second.zip")
    assert first["manifest"] == second["manifest"]
    assert first["sha256"] == second["sha256"]


def test_project_export_rejects_external_resource_directory(recorded_project):
    project, _, _ = recorded_project
    external = project.parent / "external"
    external.mkdir()
    (external / "secret.txt").write_text("DO-NOT-EXPORT")
    write_json(project / "interface.json", {"resource": [{"path": [str(external)]}]})
    with pytest.raises(DevError) as caught:
        Reports(project).export_project("out.zip")
    assert caught.value.code == "EXPORT_PATH_ESCAPE"


def test_project_export_rejects_uncurated_case_images(recorded_project):
    project, store, _ = recorded_project
    cases = project / "dev/cases"
    cases.mkdir(parents=True)
    image = store.directory("observations", "obs_before") / "image.png"
    write_json(cases / "mail.json", [{"node": "Open", "image": str(image), "expected_hit": True}])
    with pytest.raises(DevError) as caught:
        Reports(project).export_project("out.zip")
    assert caught.value.code == "UNEXPORTED_REFERENCE"


def test_invalid_run_records_are_reported_as_warnings(recorded_project):
    project, store, _ = recorded_project
    bad = store.directory("runs", "run_bad") / "record.json"
    write_json(bad, [])
    result = Reports(project).list_runs()
    assert result["total"] == 1
    assert result["warnings"][0]["run_id"] == "run_bad"


def test_limit_is_bounded(recorded_project):
    project, _, _ = recorded_project
    for value in [0, -1, 1001, True]:
        with pytest.raises(DevError):
            Reports(project).list_runs(value)


def test_resource_directory_symlink_cannot_expose_runtime_metadata(recorded_project):
    project, store, _ = recorded_project
    alias = project / "resource-alias"
    try:
        alias.symlink_to(store.root, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is not enabled for this system user")
    write_json(project / "interface.json", {"resource": [{"path": ["resource-alias"]}]})
    with pytest.raises(DevError) as caught:
        Reports(project).export_project("out.zip")
    assert caught.value.code == "UNSUPPORTED_EXPORT"
