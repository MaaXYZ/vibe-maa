"""Prepare a maintenance trial from an unchanged, previously recorded submission."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import json5
from grader import DRAWIO_STARTING_STATES, grade_drawio, write_result
from regrade import verify_manifest

from vibe_maa.reports import Reports

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "examples/agent-generated/drawio-low-isolated"


def prepare_change_inputs(project: Path, published: Path) -> dict[str, Any]:
    """Supply observed failure frames and ground truth, never a replacement template or repair."""
    sources = {
        "PreviousDialog.png": ROOT / "docs/records/drawio-recovery-ci-storage.png",
        "ChangedDialog.png": ROOT / "docs/records/drawio-recovery-local-failure.png",
    }
    manifest_path = project / "dev/cases/AgentCanvas.json"
    cases = json.loads(manifest_path.read_text())
    metadata = {}
    with zipfile.ZipFile(published / "change-inputs.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name, source in sources.items():
            destination = project / "dev/cases" / name
            shutil.copyfile(source, destination)
            archive.write(source, name)
            metadata[name] = {"sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            cases.append({"node": "SplashStorage", "image": name, "expected_hit": True})
        manifest_path.write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
        archive.write(manifest_path, "AgentCanvas.json")
    return {
        "kind": "full_observed_frames_not_a_solution",
        "files": metadata,
        "labels": "Both frames visibly show the storage dialog represented by the existing SplashStorage node",
        "source_failure": "drawio-recovery-local-replay.json",
    }


def validate_change_contract(
    project: Path, seed: Path, expected_urls: dict[str, str] | None = None
) -> dict[str, Any]:
    """Keep regression ground truth outside agent control, without prescribing recognition."""
    violations = []
    original = json5.loads((seed / "dev/cases/AgentCanvas.json").read_text())
    current = json5.loads((project / "dev/cases/AgentCanvas.json").read_text())
    expected_frames = {
        "PreviousDialog.png": ROOT / "docs/records/drawio-recovery-ci-storage.png",
        "ChangedDialog.png": ROOT / "docs/records/drawio-recovery-local-failure.png",
    }
    expected = original + [
        {"node": "SplashStorage", "image": name, "expected_hit": True} for name in expected_frames
    ]
    for case in expected:
        if case not in current:
            violations.append(f"Changed or removed regression case: {case['image']}")
        source = expected_frames.get(case["image"], seed / "dev/cases" / case["image"])
        actual = project / "dev/cases" / case["image"]
        if (
            actual.is_symlink()
            or not actual.is_file()
            or actual.read_bytes() != source.read_bytes()
        ):
            violations.append(f"Changed or removed labelled screenshot: {case['image']}")
    previous_task = json5.loads((seed / "dev/tasks/AgentCanvas.json").read_text())
    task = json5.loads((project / "dev/tasks/AgentCanvas.json").read_text())
    for name in ("entry", "goal", "max_attempts"):
        if task.get(name) != previous_task[name]:
            violations.append(f"Changed protected task field: {name}")
    if set(task.get("scenarios") or {}) != set(previous_task["scenarios"]):
        violations.append("Changed required starting scenarios")
    if expected_urls is not None and task.get("scenario_urls") != expected_urls:
        violations.append("Task must declare both exact current starting URLs")
    return {
        "passed": not violations,
        "violations": violations,
        "scope": "original_goal_identity_budget_and_regression_ground_truth",
    }


def initialize_recovery(project: Path, published: Path, seed: Path = SEED) -> dict[str, Any]:
    verified = verify_manifest(seed)
    if not verified["present"]:
        raise ValueError("Recovery seed requires its original source manifest")
    manifest = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
    project.mkdir()
    with zipfile.ZipFile(published / "starter.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(seed / "manifest.json", "manifest.json")
        for entry in manifest["files"]:
            relative = entry["path"]
            destination = project / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(seed / relative, destination)
            archive.write(destination, relative)
    return {
        "mode": "maintenance_of_authorized_seed_not_independent_from_scratch",
        "seed": seed.relative_to(ROOT).as_posix(),
        "seed_workflow": json.loads((seed / "ORIGIN.json").read_text())["original_workflow"],
        "seed_manifest": verified,
        "seed_files": manifest["files"],
        "starting_states": list(DRAWIO_STARTING_STATES),
        "repetitions_per_state": 3,
    }


def record_baseline(
    project: Path, published: Path, url: str, browser_executable: str | None
) -> dict[str, Any]:
    baseline = grade_drawio(
        project,
        url,
        browser_executable,
        starting_states=DRAWIO_STARTING_STATES,
        repetitions=1,
        timeout_seconds=12,
    )
    write_result(published / "baseline.json", baseline)
    reports = Reports(project)
    for check in baseline["checks"]:
        run_id = check["run_id"]
        reports.export_run(run_id, str(published / f"baseline-evidence-{run_id}.zip"))
    # Infrastructure or a changed seed must fail before spending model budget.
    checks = {check["starting_state"]: check for check in baseline["checks"]}
    if baseline["blocked_external_requests"]:
        raise ValueError("Recovery baseline made external requests; refusing model execution")
    if not checks["blank"]["passed"] or checks["splash"]["passed"]:
        raise ValueError("Recovery baseline must pass blank and fail splash before model execution")
    return baseline
