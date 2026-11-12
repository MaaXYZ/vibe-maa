"""Replay a previously generated MaaFW project; never call a language model."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from grader import drawio_server, grade_drawio, grade_mailbox, write_result

from vibe_maa.reports import Reports


def source_hashes(project: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        if ".maa-dev" in relative.parts or not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(project):
            raise ValueError("Replay sources must stay inside the submitted project")
        with path.open("rb") as stream:
            hashes[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashes


def verify_manifest(project: Path) -> dict[str, Any]:
    path = project / "manifest.json"
    if not path.is_file():
        return {"present": False, "verified_files": 0}
    metadata = json.loads(path.read_text(encoding="utf-8"))
    entries = metadata.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Original manifest must list its exported files")
    verified = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("Invalid file entry in original manifest")
        name = PurePosixPath(entry["path"])
        if (
            name.is_absolute()
            or ".." in name.parts
            or "\\" in entry["path"]
            or ":" in entry["path"]
        ):
            raise ValueError("Original manifest path must remain inside the project")
        if name.as_posix() in verified:
            raise ValueError("Original manifest contains a duplicate file path")
        source = project.joinpath(*name.parts)
        if (
            not source.is_file()
            or source.is_symlink()
            or not source.resolve().is_relative_to(project)
        ):
            raise ValueError(f"Original manifest file is missing or outside the project: {name}")
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry.get("sha256") or source.stat().st_size != entry.get("size"):
            raise ValueError(f"Original manifest checksum mismatch: {name}")
        verified.add(name.as_posix())
    return {
        "present": True,
        "verified_files": len(verified),
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def replay(
    project: Path,
    output: Path,
    target: str,
    cache: Path,
    browser_executable: str | None = None,
    source_archive: Path | None = None,
) -> dict[str, Any]:
    project, output = project.resolve(), output.resolve()
    if output.exists():
        raise ValueError("Choose a new output directory for each replay")
    original_manifest = verify_manifest(project)
    before = source_hashes(project)
    if not before:
        raise ValueError("The submitted project is empty")
    output.mkdir(parents=True)
    upstream = None
    if target == "drawio":
        with drawio_server(cache.resolve()) as (url, upstream):
            grade = grade_drawio(project, url, browser_executable)
    else:
        grade = grade_mailbox(project)
    after = source_hashes(project)
    result = {
        "schema_version": 1,
        "mode": "replay_no_model_calls",
        "grade": grade,
        "source_unchanged": before == after,
        "source_hashes": before,
        "upstream": upstream,
        "original_manifest": original_manifest,
    }
    if source_archive is not None:
        with source_archive.open("rb") as stream:
            result["source_archive_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    if before != after:
        grade["passed"] = False
    reports = Reports(project)
    for check in grade.get("checks", []):
        run_id = check.get("run_id")
        if run_id:
            reports.export_run(run_id, str(output / f"evidence-{run_id}.zip"))
            record = reports.store.read("runs", run_id)
            last = record.get("last_recognition", {}).get("image_path")
            if last:
                shutil.copyfile(last, output / f"last-recognition-{run_id}.png")
    write_result(output / "replay.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", choices=["drawio", "mailbox"], default="drawio")
    parser.add_argument("--cache", type=Path, default=Path("work/targets"))
    parser.add_argument("--browser-executable")
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    result = replay(
        args.project,
        args.output,
        args.target,
        args.cache,
        args.browser_executable,
        args.source_archive,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["grade"]["passed"] and result["source_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
