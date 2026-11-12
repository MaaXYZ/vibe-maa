"""A bounded, read-only orientation snapshot for a newly attached coding agent."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import json5

from . import __version__
from .errors import DevError

_JSON_LIMIT = 2 * 1024 * 1024
_PRIVATE = re.compile(
    r"(?i)(?:authorization|api[_-]?key|(?:access|refresh|session)[_-]?token)\s*[\"']?\s*[:=]"
)


def _text(value: Any, limit: int = 240) -> str:
    text = str(value)
    text = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://[REDACTED]@", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", text)
    if _PRIVATE.search(text):
        return "[Private credential-like content omitted]"
    return text if len(text) <= limit else text[:limit] + "…"


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())


class _Snapshot:
    def __init__(self, project: Path, max_bytes: int):
        self.project = project
        self.limit = max_bytes - 128
        self.warnings: list[dict[str, Any]] = []
        self.omitted: dict[str, int] = {}
        self.result: dict[str, Any] = {
            "schema_version": 1,
            "tool": "vibe-maa",
            "version": __version__,
            "project": {"path": _text(project, 500), "exists": project.is_dir()},
            "observation_policy": "Retained frames are historical. Observe again before input.",
            "next_steps": [],
            "budget": {
                "max_bytes": max_bytes,
                "envelope_reserve_bytes": 128,
                "serialized_bytes": 0,
                "truncated": False,
                "omitted": self.omitted,
            },
        }

    def warn(self, code: str, path: Path | None = None) -> None:
        value: dict[str, Any] = {"code": code}
        if path is not None:
            value["path"] = _text(path.relative_to(self.project).as_posix(), 180)
        self.warnings.append(value)

    def inside(self, path: Path) -> bool:
        return path.resolve().is_relative_to(self.project)

    def read(self, path: Path, limit: int = _JSON_LIMIT) -> dict[str, Any] | None:
        if not self.inside(path):
            self.warn("PATH_OUTSIDE_PROJECT")
            return None
        try:
            if path.stat().st_size > limit:
                self.warn("CONTEXT_FILE_TOO_LARGE", path)
                return None
            with path.open("rb") as stream:
                content = stream.read(limit + 1)
            if len(content) > limit:
                self.warn("CONTEXT_FILE_TOO_LARGE", path)
                return None
            value = json5.loads(content.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Expected object")
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError):
            self.warn("INVALID_CONTEXT_FILE", path)
            return None

    def add(self, key: str, value: Any) -> bool:
        self.result[key] = value
        # Keep room for final omission counts and warning metadata.
        if _size(self.result) <= self.limit - 180:
            return True
        self.result.pop(key)
        self.omitted[key] = self.omitted.get(key, 0) + 1
        return False

    def items(self, key: str, values: list[dict[str, Any]]) -> None:
        selected = []
        self.result[key] = selected
        for index, value in enumerate(values):
            selected.append(value)
            if _size(self.result) > self.limit - 180:
                selected.pop()
                self.omitted[key] = self.omitted.get(key, 0) + len(values) - index
                break

    def finish(self) -> dict[str, Any]:
        if self.warnings:
            self.items("warnings", self.warnings[:8])
            if len(self.warnings) > 8:
                self.omitted["warnings"] = self.omitted.get("warnings", 0) + len(self.warnings) - 8
        self.result["budget"]["truncated"] = bool(self.omitted)
        for _ in range(3):
            self.result["budget"]["serialized_bytes"] = _size(self.result)
        if _size(self.result) > self.limit:
            raise DevError("CONTEXT_BUDGET_TOO_SMALL", "Use a larger context byte budget")
        return self.result


def _recent_paths(snapshot: _Snapshot, kind: str, limit: int = 3) -> list[Path]:
    root = snapshot.project / ".maa-dev" / kind
    if not snapshot.inside(root):
        snapshot.warn("PATH_OUTSIDE_PROJECT")
        return []
    try:
        paths = [p for p in root.glob("*/record.json") if snapshot.inside(p)]
        return sorted(paths, key=lambda p: p.stat().st_mtime_ns, reverse=True)[:limit]
    except OSError:
        snapshot.warn("CONTEXT_HISTORY_UNAVAILABLE")
        return []


def _error(value: Any) -> dict[str, str] | None:
    if not value:
        return None
    return {
        "code": _text(value.get("code", "SAVED_OPERATION_FAILED"), 80)
        if isinstance(value, dict)
        else "SAVED_OPERATION_FAILED",
        "detail": "Read the referenced artifact for the complete diagnostic.",
    }


def _task(snapshot: _Snapshot, name: str) -> dict[str, Any]:
    from .tasks import TaskManager

    manager = TaskManager(snapshot.project)
    path = manager._path(name)
    raw = snapshot.read(path, 64 * 1024)
    if raw is None:
        if not path.exists():
            raise DevError("TASK_NOT_FOUND", f"Task not found: {name}")
        return {
            "name": name,
            "state": "unknown",
            "scope": "unknown",
            "error": {"code": "TASK_CONTEXT_UNAVAILABLE"},
        }
    # TaskManager is authoritative for acceptance. Bound its input metadata before
    # calling it rather than turning a huge report into an unverified pass.
    directory = snapshot.project / ".maa-dev/tasks" / name / "attempts"
    safe = snapshot.inside(directory)
    total = 0
    if safe:
        for attempt_path in directory.glob("*.json"):
            attempt = snapshot.read(attempt_path, 256 * 1024)
            if attempt is None:
                safe = False
                break
            total += attempt_path.stat().st_size
            report = attempt.get("report_path")
            if isinstance(report, str):
                report_path = snapshot.project / report
                try:
                    safe = (
                        snapshot.inside(report_path) and report_path.stat().st_size <= _JSON_LIMIT
                    )
                except OSError:
                    # Missing proof must be evaluated by TaskManager, never accepted here.
                    pass
            if not safe or total > 4 * _JSON_LIMIT:
                safe = False
                break
    try:
        context = (
            manager.context(name)
            if safe
            else {
                **raw,
                "state": "unknown",
                "scope": "unknown",
                "error": {"code": "TASK_EVIDENCE_EXCEEDS_CONTEXT_LIMIT"},
            }
        )
    except (DevError, OSError, ValueError) as exc:
        context = {
            **raw,
            "state": "unknown",
            "scope": "unknown",
            "error": exc.as_dict()
            if isinstance(exc, DevError)
            else {"code": "INVALID_TASK_HISTORY"},
        }
    result: dict[str, Any] = {
        "name": name,
        "objective": _text(context.get("objective", ""), 360),
        "entry": _text(context.get("entry", ""), 100),
        "goal": _text(context.get("goal", ""), 100),
        "state": _text(context.get("state", "unknown"), 80),
        "scope": _text(context.get("scope", "unknown"), 80),
        "acceptance_scope": "configured_checks_only",
        "constraints_status": _text(context.get("constraints_status", "not_checked"), 80),
        "freshness": _text(context.get("freshness", "unknown"), 80),
        "attempt_count": context.get("attempt_count"),
        "remaining_attempts": context.get("remaining_attempts"),
    }
    for key in ("required_scenarios", "completed_scenarios", "missing_scenarios"):
        values = context.get(key, [])
        values = values if isinstance(values, list) else []
        result[key] = [_text(value, 80) for value in values[:5]]
        result[key + "_count"] = len(values)
        if len(values) > 5:
            snapshot.omitted[key] = len(values) - 5
    latest = context.get("latest_attempt")
    if isinstance(latest, dict):
        result["latest_attempt"] = {
            key: _text(latest[key], 100) if isinstance(latest[key], str) else latest[key]
            for key in (
                "attempt_id",
                "run_id",
                "status",
                "execution_status",
                "goal_status",
                "scenario",
            )
            if isinstance(latest.get(key), (str, bool, int))
        }
        if latest.get("error"):
            result["latest_attempt"]["error"] = _error(latest["error"])
    if context.get("error"):
        result["error"] = _error(context["error"])
    return result


def _focus(snapshot: _Snapshot, value: dict[str, Any]) -> None:
    snapshot.result["task"] = value
    if _size(snapshot.result) <= snapshot.limit - 350:
        return
    for key in (
        "required_scenarios",
        "completed_scenarios",
        "missing_scenarios",
        "objective",
        "entry",
        "goal",
    ):
        if key in value:
            value.pop(key)
            snapshot.omitted["task_fields"] = snapshot.omitted.get("task_fields", 0) + 1
        if _size(snapshot.result) <= snapshot.limit - 350:
            break
    latest = value.get("latest_attempt", {})
    for key in ("scenario", "run_id", "attempt_id"):
        if _size(snapshot.result) <= snapshot.limit - 350:
            break
        if key in latest:
            latest.pop(key)
            snapshot.omitted["task_latest_attempt_fields"] = (
                snapshot.omitted.get("task_latest_attempt_fields", 0) + 1
            )


def _daemon(snapshot: _Snapshot) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not (snapshot.project / ".maa-dev/daemon.json").is_file():
        return {"running": False}, []
    if not snapshot.inside(snapshot.project / ".maa-dev/daemon.json"):
        snapshot.warn("PATH_OUTSIDE_PROJECT")
        return {"running": False}, []
    if snapshot.read(snapshot.project / ".maa-dev/daemon.json", 64 * 1024) is None:
        return {"running": False}, []
    from .daemon import _exchange, _read_state, daemon_status

    try:
        status = daemon_status(snapshot.project)
        if not isinstance(status, dict):
            raise TypeError("Invalid service status")
    except (DevError, KeyError, TypeError, AttributeError):
        snapshot.warn("LIVE_SESSIONS_UNAVAILABLE")
        return {"running": False}, []
    summary = {
        key: _text(status[key], 80) if isinstance(status[key], str) else status[key]
        for key in ("running", "compatible", "stopping", "version")
        if isinstance(status.get(key), (str, int, bool))
    }
    sessions = []
    if status.get("running") and status.get("compatible") and not status.get("stopping"):
        try:
            state = _read_state(snapshot.project)
            response = _exchange(state, "session.list", {}, 1) if state else {}
            result = response.get("result")
            if response.get("ok") and isinstance(result, dict):
                current = result.get("sessions", [])
                if not isinstance(current, list):
                    raise ValueError("Invalid session list")
                for session in current[:10]:
                    if not isinstance(session, dict) or not isinstance(
                        session.get("session_id"), str
                    ):
                        continue
                    sessions.append(
                        {
                            key: _text(session[key], 100)
                            if isinstance(session[key], str)
                            else session[key]
                            for key in ("session_id", "backend", "generation")
                            if isinstance(session.get(key), (str, int, bool))
                        }
                    )
                if len(current) > 10:
                    snapshot.omitted["live_sessions"] = len(current) - 10
        except (DevError, ValueError, TypeError, AttributeError):
            snapshot.warn("LIVE_SESSIONS_UNAVAILABLE")
    return summary, sessions


def _inventory(snapshot: _Snapshot) -> dict[str, Any]:
    metadata = snapshot.read(snapshot.project / "interface.json", 128 * 1024) or {}
    resources = metadata.get("resource", [])
    paths = []
    for resource in resources if isinstance(resources, list) else []:
        if isinstance(resource, dict) and isinstance(resource.get("path"), list):
            paths.extend(p for p in resource["path"] if isinstance(p, str))
    paths = list(dict.fromkeys(paths or ["resource"]))
    names = set()
    count = 0
    bundles = []
    for value in paths[:8]:
        bundle = snapshot.project / value
        if not snapshot.inside(bundle):
            snapshot.warn("RESOURCE_PATH_OUTSIDE_PROJECT")
            continue
        bundles.append(bundle.resolve().relative_to(snapshot.project).as_posix())
        pipeline = bundle / "pipeline"
        if not snapshot.inside(pipeline):
            snapshot.warn("RESOURCE_PATH_OUTSIDE_PROJECT")
            continue
        for path in pipeline.rglob("*"):
            if path.suffix not in {".json", ".jsonc"} or not path.is_file():
                continue
            if count >= 256:
                snapshot.omitted["pipeline_files"] = snapshot.omitted.get("pipeline_files", 0) + 1
                continue
            count += 1
            data = snapshot.read(path, 256 * 1024)
            if data:
                names.update(
                    _text(name, 100)
                    for name in data
                    if isinstance(name, str) and not name.startswith("$")
                )
    if len(paths) > 8:
        snapshot.omitted["resource_bundles"] = len(paths) - 8
    if len(names) > 25:
        snapshot.omitted["node_names"] = len(names) - 25
    return {
        "resource_bundles": bundles,
        "pipeline_files_read": count,
        "node_count": len(names),
        "node_names": sorted(names)[:25],
        "node_names_omitted": max(0, len(names) - 25),
    }


def show(project: Path, task: str | None = None, max_bytes: int = 12000) -> dict[str, Any]:
    if type(max_bytes) is not int or not 2000 <= max_bytes <= 64000:
        raise DevError("INVALID_ARGUMENT", "max_bytes must be an integer in [2000,64000]")
    snapshot = _Snapshot(Path(project).resolve(), max_bytes)
    if task is not None:
        focused = _task(snapshot, task)
        # A compact task's verdict and evidence scope take priority over optional history.
        _focus(snapshot, focused)
        snapshot.result["next_steps"] = [f"vibe-maa task context {task}"]
    daemon, sessions = _daemon(snapshot)
    snapshot.add("daemon", daemon)
    if sessions:
        snapshot.items("live_sessions", sessions)
        snapshot.result["next_steps"].append(
            f"vibe-maa observe --session {sessions[0]['session_id']}"
        )
    elif daemon.get("running") and daemon.get("compatible") is False:
        snapshot.result["next_steps"].append("vibe-maa daemon stop")
    else:
        snapshot.result["next_steps"].append("vibe-maa capabilities --method session.open")
    snapshot.add("inventory", _inventory(snapshot))
    if task is None:
        tasks = []
        directory = snapshot.project / "dev/tasks"
        if snapshot.inside(directory):
            task_paths = sorted(directory.glob("*.json"))
            if len(task_paths) > 20:
                snapshot.omitted["tasks"] = len(task_paths) - 20
            for path in task_paths[:20]:
                spec = snapshot.read(path, 64 * 1024)
                if spec:
                    tasks.append(
                        {
                            "name": _text(path.stem, 80),
                            "objective": _text(spec.get("objective", ""), 160),
                            "verification": "not_revalidated_in_overview",
                            "next_command": f"vibe-maa task context {_text(path.stem, 80)}",
                        }
                    )
        snapshot.items("tasks", tasks)
    runs = []
    for path in _recent_paths(snapshot, "runs"):
        record = snapshot.read(path)
        if record:
            item = {
                key: _text(record[key], 100)
                for key in (
                    "run_id",
                    "entry",
                    "execution_status",
                    "goal_status",
                    "backend",
                    "scope",
                    "finished_at",
                    "before_observation",
                    "after_observation",
                )
                if key in record
            } | {"next_command": f"vibe-maa report show {path.parent.name}"}
            for key in ("failure", "verification_failure", "error", "verification_error"):
                if record.get(key):
                    item[key] = _error(record[key])
            runs.append(item)
    snapshot.items("recent_runs", runs)
    observations = []
    for path in _recent_paths(snapshot, "observations"):
        record = snapshot.read(path, 256 * 1024)
        if not record:
            continue
        if not isinstance(record.get("image_path"), str):
            snapshot.warn("HISTORICAL_IMAGE_UNAVAILABLE_OR_MODIFIED", path)
            continue
        image_path = Path(record["image_path"])
        image_path = snapshot.project / image_path
        try:
            valid = (
                snapshot.inside(image_path)
                and image_path.is_file()
                and image_path.stat().st_size <= 8 * _JSON_LIMIT
            )
            if valid:
                with image_path.open("rb") as stream:
                    valid = hashlib.file_digest(stream, "sha256").hexdigest() == record.get(
                        "sha256"
                    )
        except OSError:
            valid = False
        if not valid:
            snapshot.warn("HISTORICAL_IMAGE_UNAVAILABLE_OR_MODIFIED", path)
            continue
        observations.append(
            {
                "observation_id": path.parent.name,
                "session_id": _text(record.get("session_id", ""), 100),
                "image_path": image_path.relative_to(snapshot.project).as_posix(),
                "fresh": False,
                "input_authorization": False,
            }
        )
    snapshot.items("historical_observations", observations)
    actions = []
    for path in _recent_paths(snapshot, "actions"):
        record = snapshot.read(path, 256 * 1024)
        if record:
            item = {
                "action_id": path.parent.name,
                "status": _text(record.get("status", "unknown"), 80),
            }
            if record.get("error"):
                item["error"] = _error(record["error"])
            actions.append(item)
    snapshot.items("recent_actions", actions)
    return snapshot.finish()
