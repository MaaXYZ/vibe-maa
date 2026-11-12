"""Compact experiment facts and portable, checksummed project/evidence archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import json5

from .errors import DevError
from .evidence import EvidenceStore

_PATH_FIELDS = {"image_path", "report_path"}
_OBSERVATION_FIELDS = {
    "observation_id",
    "before_observation",
    "after_observation",
    "source_observation",
}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Reports:
    def __init__(self, project: Path):
        self.project = Path(project).resolve()
        self.store = EvidenceStore(self.project)

    def list_runs(self, limit: int = 20) -> dict[str, Any]:
        if type(limit) is not int or not 0 < limit <= 1000:
            raise DevError("INVALID_ARGUMENT", "limit must be an integer in [1,1000]")
        runs = []
        warnings = []
        for path in (self.store.root / "runs").glob("*/record.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    raise ValueError("Run record must be an object")
                runs.append(self._brief(record))
            except (OSError, ValueError, TypeError) as exc:
                warnings.append({"run_id": path.parent.name, "error": str(exc)})
        runs.sort(
            key=lambda item: (item.get("started_at") or "", item.get("run_id") or ""), reverse=True
        )
        return {"runs": runs[:limit], "total": len(runs), "warnings": warnings}

    @staticmethod
    def _brief(record: dict[str, Any]) -> dict[str, Any]:
        fields = [
            "run_id",
            "entry",
            "started_at",
            "finished_at",
            "execution_status",
            "goal_status",
            "backend",
            "device",
            "scope",
            "runtime",
            "inputs_changed",
            "report_path",
        ]
        return {key: record.get(key) for key in fields}

    def summary(self, run_id: str) -> dict[str, Any]:
        record = self.store.read("runs", run_id)
        events = record.get("events", [])
        waiting_for = []
        last_failed_list = None
        for event in events:
            if event.get("message") == "Node.NextList.Failed":
                last_failed_list = event
        if last_failed_list:
            candidates = last_failed_list.get("details", {}).get("list", [])
            waiting_for = [
                item.get("name") if isinstance(item, dict) else item for item in candidates
            ]
            waiting_for = [item for item in waiting_for if isinstance(item, str)]
        nodes = record.get("nodes", [])
        last_recognition = record.get("last_recognition")
        missing = [
            {
                "sequence": event.get("sequence"),
                "message": event.get("message"),
                "reason": event["missing_evidence"],
            }
            for event in events
            if event.get("missing_evidence")
        ]
        return {
            **self._brief(record),
            "last_node": nodes[-1] if nodes else None,
            "waiting_for": waiting_for,
            "last_recognition": last_recognition,
            "before_observation": record.get("before_observation"),
            "after_observation": record.get("after_observation"),
            "error": record.get("error"),
            "failure": record.get("failure"),
            "verification_error": record.get("verification_error"),
            "verification_failure": record.get("verification_failure"),
            "evidence": {
                "events": len(events),
                "missing": missing[-20:],
                "missing_count": len(missing),
                "truncated_events": record.get("truncated_events", 0),
                "truncated_images": record.get("truncated_images", 0),
                "omitted_nodes": record.get("omitted_nodes", 0),
            },
            "interpretation": "Recorded facts only; no root cause was inferred.",
        }

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.project):
            raise DevError("EXPORT_PATH_ESCAPE", "An export input points outside the project")
        if not resolved.is_file():
            raise DevError("EVIDENCE_MISSING", f"Missing export input: {path}")
        return resolved

    def _tree(self, directory: Path) -> list[Path]:
        if directory.is_symlink():
            if not directory.resolve().is_relative_to(self.project):
                raise DevError("EXPORT_PATH_ESCAPE", "An export symlink leaves the project")
            raise DevError("UNSUPPORTED_EXPORT", "Symlinks must be materialized before export")
        if not directory.resolve().is_relative_to(self.project):
            raise DevError("EXPORT_PATH_ESCAPE", "An export directory points outside the project")
        paths = []
        for path in directory.rglob("*"):
            if path.is_symlink():
                if not path.resolve().is_relative_to(self.project):
                    raise DevError("EXPORT_PATH_ESCAPE", "An export symlink leaves the project")
                raise DevError("UNSUPPORTED_EXPORT", "Symlinks must be materialized before export")
            if path.is_file() or path.is_symlink():
                self._inside(path)
                paths.append(path)
        return paths

    def _load_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DevError("INVALID_EVIDENCE", f"Cannot read JSON {path}: {exc}") from exc

    def _reference(self, value: str) -> Path:
        if re.match(r"^[A-Za-z]:[\\/]", value) and os.name != "nt":
            raise DevError("EVIDENCE_MISSING", "Evidence uses a different machine's absolute path")
        path = Path(value)
        return self._inside(path if path.is_absolute() else self.project / path)

    def _verify_images(self, value: Any, expected: dict[Path, str]) -> None:
        if isinstance(value, dict):
            if "image_path" in value:
                path = self._reference(value["image_path"])
                digest = value.get("sha256")
                if not isinstance(digest, str) or _digest(path) != digest:
                    raise DevError("EVIDENCE_MODIFIED", f"Image digest mismatch: {path.name}")
                if path in expected and expected[path] != digest:
                    raise DevError("EVIDENCE_MODIFIED", "Conflicting image digests in evidence")
                expected[path] = digest
            for item in value.values():
                self._verify_images(item, expected)
        elif isinstance(value, list):
            for item in value:
                self._verify_images(item, expected)

    def _rewrite(self, value: Any, files: dict[Path, str], *, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {name: self._rewrite(item, files, key=name) for name, item in value.items()}
        if isinstance(value, list):
            return [self._rewrite(item, files, key=key) for item in value]
        if isinstance(value, str):
            if key in _PATH_FIELDS:
                path = self._reference(value)
                if path not in files:
                    raise DevError(
                        "UNEXPORTED_REFERENCE", f"Referenced artifact is not selected: {path.name}"
                    )
                return files[path]
            # Native details may mention source paths as text, not linked artifact files.
            project_string = str(self.project)
            if value == project_string or value.startswith(project_string + os.sep):
                path = Path(value).resolve()
                if not path.is_relative_to(self.project):
                    raise DevError("EXPORT_PATH_ESCAPE", "Recorded path leaves the project")
                return files.get(path, "project:" + path.relative_to(self.project).as_posix())
        return value

    @staticmethod
    def _artifact_links(value: Any) -> tuple[set[str], set[str]]:
        observations, actions = set(), set()

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                for key, sub in item.items():
                    if key in _OBSERVATION_FIELDS and isinstance(sub, str):
                        observations.add(sub)
                    if key == "action_id" and isinstance(sub, str) and sub.startswith("action_"):
                        actions.add(sub)
                    if key == "action_ids" and isinstance(sub, list):
                        actions.update(action for action in sub if isinstance(action, str))
                    visit(sub)
            elif isinstance(item, list):
                for sub in item:
                    visit(sub)

        visit(value)
        return observations, actions

    def export_run(self, run_id: str, output: str) -> dict[str, Any]:
        report = self.store.read("runs", run_id)
        directory = self.store.directory("runs", run_id)
        sources: dict[Path, str] = {}

        def add_directory(kind: str, artifact_id: str) -> None:
            root = self.store.directory(kind, artifact_id)
            self.store.read(kind, artifact_id)  # Require a complete, named artifact record.
            for path in self._tree(root):
                sources[self._inside(path)] = (
                    f"evidence/{kind}/{artifact_id}/{path.relative_to(root).as_posix()}"
                )

        add_directory("runs", run_id)
        observations, actions = self._artifact_links(report)
        for aid in sorted(actions):
            action = self.store.read("actions", aid)
            add_directory("actions", aid)
            found, _ = self._artifact_links(action)
            observations.update(found)
        for oid in sorted(observations):
            add_directory("observations", oid)
        rewritten: dict[Path, bytes] = {}
        expected: dict[Path, str] = {}
        for source in sources:
            if source.suffix == ".json" and "input-files" not in source.parts:
                content = self._load_json(source)
                self._verify_images(content, expected)
                content = self._rewrite(content, sources)
                rewritten[source] = _json_bytes(content)
            elif source.suffix == ".jsonl":
                lines = []
                for line in source.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                    except ValueError as exc:
                        raise DevError(
                            "INVALID_EVIDENCE", "Event log contains invalid JSON"
                        ) from exc
                    self._verify_images(item, expected)
                    lines.append(
                        json.dumps(
                            self._rewrite(item, sources), ensure_ascii=False, allow_nan=False
                        )
                    )
                rewritten[source] = ("\n".join(lines) + ("\n" if lines else "")).encode()
        return self._archive(
            output,
            sources,
            rewritten,
            {
                "kind": "run_evidence",
                "run_id": run_id,
                "scope": report.get("scope", "unknown"),
                "execution_status": report.get("execution_status"),
                "goal_status": report.get("goal_status"),
                "report": sources[self._inside(directory / "record.json")],
            },
            expected,
        )

    def _project_sources(self) -> dict[Path, str]:
        interface = self.project / "interface.json"
        data = self._load_json(self._inside(interface))
        resources = data.get("resource") if isinstance(data, dict) else None
        if not isinstance(resources, list) or not resources:
            raise DevError("INVALID_PROJECT", "interface.json needs at least one resource bundle")
        sources = {self._inside(interface): "interface.json"}
        for resource in resources:
            paths = resource.get("path", []) if isinstance(resource, dict) else []
            if isinstance(paths, str):
                paths = [paths]
            if not isinstance(paths, list) or not paths:
                raise DevError("INVALID_PROJECT", "Every resource needs an explicit path")
            for value in paths:
                if not isinstance(value, str) or "{" in value or "}" in value:
                    raise DevError(
                        "UNSUPPORTED_EXPORT", "Resource paths must be project-relative literals"
                    )
                bundle = self.project / value
                if not bundle.is_dir():
                    raise DevError("INVALID_PROJECT", f"Resource directory does not exist: {value}")
                if bundle.resolve() == self.project:
                    raise DevError("UNSUPPORTED_EXPORT", "Use a resource subdirectory for export")
                for source in self._tree(bundle):
                    if (
                        ".maa-dev" in source.relative_to(self.project).parts
                        or ".git" in source.parts
                    ):
                        raise DevError(
                            "INVALID_PROJECT", "Runtime metadata cannot be a resource bundle"
                        )
                    sources[self._inside(source)] = source.relative_to(self.project).as_posix()
        if not any(
            "pipeline" in PurePosixPath(name).parts and name.endswith((".json", ".jsonc"))
            for name in sources.values()
        ):
            raise DevError(
                "INVALID_PROJECT", "No Pipeline files were found in the project resources"
            )
        for name in ("tasks", "cases", "assets"):
            directory = self.project / "dev" / name
            for source in self._tree(directory):
                sources[self._inside(source)] = source.relative_to(self.project).as_posix()
        return sources

    def export_project(self, output: str) -> dict[str, Any]:
        sources = self._project_sources()
        rewritten: dict[Path, bytes] = {}
        expected: dict[Path, str] = {}
        for source, name in sources.items():
            if name.startswith("dev/assets/") and source.suffix == ".json":
                content = self._load_json(source)
                self._verify_images(content, expected)
                rewritten[source] = _json_bytes(self._rewrite(content, sources))
            elif name.startswith("dev/cases/") and source.suffix in {".json", ".jsonc"}:
                try:
                    content = json5.loads(source.read_text(encoding="utf-8"))
                except (ValueError, OSError) as exc:
                    raise DevError("INVALID_CASES", f"Cannot read case manifest: {exc}") from exc
                if isinstance(content, list):
                    for case in content:
                        if not isinstance(case, dict) or not isinstance(case.get("image"), str):
                            raise DevError("INVALID_CASES", "Case manifests need image references")
                        image = self._inside(source.parent / case["image"])
                        if image not in sources or image.suffix.lower() not in _IMAGE_SUFFIXES:
                            raise DevError(
                                "UNEXPORTED_REFERENCE",
                                "Curate case images into exported project files",
                            )
                        case["image"] = os.path.relpath(
                            sources[image], PurePosixPath(name).parent.as_posix()
                        ).replace("\\", "/")
                rewritten[source] = _json_bytes(content)
        return self._archive(
            output,
            sources,
            rewritten,
            {
                "kind": "maa_project",
                "verification": "Packaging alone does not establish task acceptance.",
            },
            expected,
        )

    def _archive(
        self,
        output: str,
        sources: dict[Path, str],
        rewritten: dict[Path, bytes],
        metadata: dict[str, Any],
        expected: dict[Path, str],
    ) -> dict[str, Any]:
        if not isinstance(output, str) or not output:
            raise DevError("INVALID_ARGUMENT", "An output ZIP path is required")
        path = Path(output)
        path = path if path.is_absolute() else self.project / path
        if path.exists() or path.is_symlink():
            raise DevError("ALREADY_EXISTS", "Export output already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {"schema_version": 1, **metadata, "files": []}
        owned = False
        try:
            with path.open("xb") as stream:
                owned = True
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for source, name in sorted(sources.items(), key=lambda pair: pair[1]):
                        self._inside(source)
                        if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                            raise DevError("EXPORT_PATH_ESCAPE", "Invalid archive path")
                        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        info.external_attr = 0o100600 << 16
                        digest, size = hashlib.sha256(), 0
                        with archive.open(info, "w", force_zip64=True) as target:
                            if source in rewritten:
                                payload = rewritten[source]
                                target.write(payload)
                                digest.update(payload)
                                size = len(payload)
                            else:
                                with source.open("rb") as original:
                                    for block in iter(lambda: original.read(1024 * 1024), b""):
                                        target.write(block)
                                        digest.update(block)
                                        size += len(block)
                        manifest["files"].append(
                            {"path": name, "sha256": digest.hexdigest(), "size": size}
                        )
                        if source in expected and digest.hexdigest() != expected[source]:
                            raise DevError("EVIDENCE_MODIFIED", "Evidence changed during export")
                    info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, _json_bytes(manifest))
        except BaseException:
            if owned:
                path.unlink(missing_ok=True)
            raise
        return {
            "output": str(path.resolve()),
            "sha256": _digest(path),
            "files": len(sources),
            "manifest": manifest,
        }
