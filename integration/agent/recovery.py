"""Prepare a maintenance trial from an unchanged, previously recorded submission."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from grader import DRAWIO_STARTING_STATES, grade_drawio, write_result
from regrade import verify_manifest

from vibe_maa.reports import Reports

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "examples/agent-generated/drawio-low-isolated"


def initialize_recovery(project: Path, published: Path) -> dict[str, Any]:
    verified = verify_manifest(SEED)
    if not verified["present"]:
        raise ValueError("Recovery seed requires its original source manifest")
    manifest = json.loads((SEED / "manifest.json").read_text(encoding="utf-8"))
    project.mkdir()
    with zipfile.ZipFile(published / "starter.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(SEED / "manifest.json", "manifest.json")
        for entry in manifest["files"]:
            relative = entry["path"]
            destination = project / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SEED / relative, destination)
            archive.write(destination, relative)
    return {
        "mode": "maintenance_of_authorized_seed_not_independent_from_scratch",
        "seed": SEED.relative_to(ROOT).as_posix(),
        "seed_workflow": "https://github.com/MaaXYZ/vibe-maa/actions/runs/43198829628",
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
