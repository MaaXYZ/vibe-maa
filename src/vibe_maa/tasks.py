"""Durable development objectives and evidence-backed acceptance checks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import json5

from .errors import DevError
from .evidence import identifier, now, write_json


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class TaskManager:
    def __init__(self, project: Path):
        self.project = Path(project).resolve()

    def _inside(self, value: str | Path) -> Path:
        path = (self.project / value).resolve()
        if not path.is_relative_to(self.project):
            raise DevError("INVALID_TASK", "Task paths must stay inside the project")
        return path

    def _name(self, name: str) -> str:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
            raise DevError("INVALID_TASK", "Task name must contain 1–80 letters, digits, _ or -")
        return name

    def _path(self, name: str) -> Path:
        return self._inside(Path("dev/tasks") / f"{self._name(name)}.json")

    def _read(self, name: str) -> tuple[dict[str, Any], str]:
        path = self._path(name)
        if not path.is_file():
            raise DevError("TASK_NOT_FOUND", f"Task not found: {name}")
        try:
            content = path.read_bytes()
            spec = json5.loads(content.decode("utf-8"))
        except (ValueError, UnicodeError, OSError) as exc:
            raise DevError("INVALID_TASK", f"Cannot read task: {exc}") from exc
        self._validate(spec)
        if spec["name"] != name:
            raise DevError("INVALID_TASK", "Task name must match its filename")
        return spec, hashlib.sha256(content).hexdigest()

    @staticmethod
    def _validate(spec: Any) -> None:
        if not isinstance(spec, dict) or spec.get("schema_version") != 1:
            raise DevError("INVALID_TASK", "Expected a version 1 task object")
        for field in ("name", "objective", "entry", "goal"):
            if not isinstance(spec.get(field), str) or not spec[field].strip():
                raise DevError("INVALID_TASK", f"Task {field} must be a nonempty string")
        if type(spec.get("max_attempts")) is not int or not 1 <= spec["max_attempts"] <= 1000:
            raise DevError("INVALID_TASK", "max_attempts must be an integer in [1,1000]")
        if not isinstance(spec.get("constraints", []), list) or any(
            not isinstance(c, str) or not c.strip() for c in spec.get("constraints", [])
        ):
            raise DevError("INVALID_TASK", "constraints must be an array of nonempty strings")
        if spec.get("cases") is not None and not isinstance(spec["cases"], str):
            raise DevError("INVALID_TASK", "cases must be a project-relative manifest path")
        scenarios = spec.get("scenarios")
        if scenarios is not None and (
            not isinstance(scenarios, dict)
            or any(
                not isinstance(label, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", label)
                or not isinstance(node, str)
                or not node.strip()
                for label, node in scenarios.items()
            )
        ):
            raise DevError(
                "INVALID_TASK", "scenarios must map valid labels to start recognition nodes"
            )

    def create(
        self,
        name: str,
        objective: str,
        entry: str,
        goal: str,
        cases: str | None = None,
        constraints: list[str] | None = None,
        max_attempts: int = 10,
        scenarios: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        path = self._path(name)
        if path.exists():
            raise DevError("TASK_EXISTS", f"Task already exists: {name}")
        if cases is not None:
            cases = self._inside(cases).relative_to(self.project).as_posix()
        spec = {
            "schema_version": 1,
            "name": name,
            "objective": objective,
            "entry": entry,
            "goal": goal,
            "cases": cases,
            "constraints": [] if constraints is None else constraints,
            "max_attempts": max_attempts,
            "scenarios": scenarios,
            "created_at": now(),
        }
        self._validate(spec)
        write_json(path, spec)
        return self.context(name)

    def _attempt_directory(self, name: str) -> Path:
        return self._inside(Path(".maa-dev/tasks") / self._name(name) / "attempts")

    def _attempts(self, name: str) -> list[dict[str, Any]]:
        records = []
        for path in self._attempt_directory(name).glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(record, dict)
                    or not isinstance(record.get("started_at"), str)
                    or not isinstance(record.get("attempt_id"), str)
                ):
                    raise ValueError("Invalid attempt record")
            except (ValueError, OSError) as exc:
                raise DevError("INVALID_TASK_HISTORY", f"Cannot read {path}: {exc}") from exc
            records.append(record)
        return sorted(records, key=lambda item: (item["started_at"], item["attempt_id"]))

    def _resource_snapshot(self, resource_path: str) -> dict[str, str]:
        bundle = self._inside(resource_path)
        return {
            str(path.relative_to(self.project)): _digest(self._inside(path))
            for path in sorted(bundle.rglob("*"))
            if path.is_file()
        }

    def _case_snapshot(self, cases: str | None) -> dict[str, str]:
        if cases is None:
            return {}
        path = self._inside(cases)
        if not path.is_file():
            raise DevError("INVALID_CASES", f"Case manifest does not exist: {cases}")
        try:
            manifest = json5.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise DevError("INVALID_CASES", str(exc)) from exc
        if not isinstance(manifest, list) or not manifest:
            raise DevError("INVALID_CASES", "Expected a nonempty array of case objects")
        paths = {path}
        for case in manifest:
            if (
                not isinstance(case, dict)
                or not isinstance(case.get("image"), str)
                or not isinstance(case.get("node"), str)
                or type(case.get("expected_hit")) is not bool
            ):
                raise DevError("INVALID_CASES", "Cases need node, image, and boolean expected_hit")
            image = self._inside(path.parent / case["image"])
            if not image.is_file():
                raise DevError("INVALID_CASES", f"Case image does not exist: {case['image']}")
            paths.add(image)
        return {str(item.relative_to(self.project)): _digest(item) for item in sorted(paths)}

    @staticmethod
    def _scenarios(spec: dict[str, Any]) -> dict[str, str | None]:
        return spec.get("scenarios") or {"default": None}

    @staticmethod
    def _target(record: dict[str, Any]) -> tuple[str, str, str, str]:
        return tuple(
            record.get(key, "unknown") for key in ("scope", "backend", "device", "resource_path")
        )

    def _report_current(self, record: dict[str, Any]) -> bool:
        try:
            path = self._inside(record["report_path"])
            if not record.get("report_sha256") or _digest(path) != record["report_sha256"]:
                return False
            report = json.loads(path.read_text(encoding="utf-8"))
            return (
                isinstance(report, dict)
                and isinstance(record.get("run_id"), str)
                and report.get("run_id") == record["run_id"]
                and report.get("session_id") == record.get("session_id")
                and report.get("entry") == record["spec"]["entry"]
                and report.get("goal") == record["spec"]["goal"]
                and report.get("execution_status") == "succeeded"
                and report.get("goal_status") == "passed"
                and report.get("inputs_changed") is False
                and report.get("inputs") == record.get("inputs")
                and report.get("backend") == record.get("backend")
                and report.get("device") == record.get("device")
                and report.get("scope") == record.get("scope")
                and report.get("resource_path") == record.get("resource_path")
            )
        except (KeyError, TypeError, ValueError, OSError, DevError):
            return False

    def _start_current(self, record: dict[str, Any], node: str | None) -> bool:
        if node is None:
            return True
        try:
            if record.get("start_inputs") != record.get("inputs"):
                return False
            start = record["start_check"]
            if start["node"] != node:
                return False
            observation_path = self._inside(start["observation_path"])
            probe_path = self._inside(start["probe_path"])
            image_path = self._inside(start["image_path"])
            if (
                _digest(observation_path) != start["observation_sha256"]
                or _digest(probe_path) != start["probe_sha256"]
                or _digest(image_path) != start["image_sha256"]
            ):
                return False
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            probe = json.loads(probe_path.read_text(encoding="utf-8"))
            return (
                probe.get("node") == node
                and probe.get("observation_id") == observation.get("observation_id")
                and observation.get("session_id") == record.get("session_id")
                and observation.get("sha256") == start["image_sha256"]
                and probe.get("result", {}).get("hit") is True
            )
        except (KeyError, TypeError, ValueError, OSError, DevError):
            return False

    def _start_check(self, service: Any, session_id: str, node: str) -> dict[str, Any]:
        session = service._session(session_id)
        definition = service._node(session, node)
        recognition = definition.get("recognition", "DirectHit")
        kind = recognition.get("type") if isinstance(recognition, dict) else recognition
        if not isinstance(kind, str) or kind == "DirectHit":
            raise DevError("INVALID_START_NODE", "Scenario start must use observable recognition")
        observation = service.observe(session_id, ocr=False)
        probe = service.probe(session_id, observation["observation_id"], node)
        oid, pid = observation["observation_id"], probe["probe_id"]
        observation_path = self._inside(f".maa-dev/observations/{oid}/record.json")
        probe_path = self._inside(f".maa-dev/probes/{pid}/record.json")
        image_path = self._inside(observation["image_path"])
        return {
            "node": node,
            "observation_id": oid,
            "probe_id": pid,
            "hit": probe["result"]["hit"],
            "observation_path": str(observation_path),
            "probe_path": str(probe_path),
            "image_path": str(image_path),
            "observation_sha256": _digest(observation_path),
            "probe_sha256": _digest(probe_path),
            "image_sha256": _digest(image_path),
        }

    def list(self) -> dict[str, Any]:
        tasks = []
        for path in sorted(self._inside("dev/tasks").glob("*.json")):
            try:
                context = self.context(path.stem)
                tasks.append(
                    {
                        key: context[key]
                        for key in (
                            "name",
                            "objective",
                            "state",
                            "scope",
                            "attempt_count",
                            "remaining_attempts",
                        )
                    }
                )
            except DevError as exc:
                tasks.append({"name": path.stem, "state": "invalid", "error": exc.as_dict()})
        return {"tasks": tasks}

    def context(self, name: str) -> dict[str, Any]:
        spec, spec_sha = self._read(name)
        attempts = self._attempts(name)
        latest = attempts[-1] if attempts else None
        remaining = max(0, spec["max_attempts"] - len(attempts))
        scenarios = self._scenarios(spec)
        state, freshness, scope = "draft", "unverified", "unknown"
        proofs: dict[str, dict[str, Any]] = {}
        scenario_attempts: dict[str, dict[str, Any]] = {}
        if latest:
            scope = latest.get("scope", "unknown")
            target = self._target(latest)
            for attempt in attempts:
                label = attempt.get("scenario", "default")
                if (
                    label in scenarios
                    and attempt.get("spec_sha256") == spec_sha
                    and self._target(attempt) == target
                ):
                    scenario_attempts[label] = attempt
            try:
                current_inputs = self._resource_snapshot(latest.get("resource_path", "resource"))
                current_cases = self._case_snapshot(spec.get("cases"))
            except (DevError, OSError):
                current_inputs, current_cases = None, None
            for label, attempt in scenario_attempts.items():
                current = (
                    attempt.get("accepted") is True
                    and attempt.get("inputs") == current_inputs
                    and attempt.get("case_inputs") == current_cases
                    and self._report_current(attempt)
                    and self._start_current(attempt, scenarios[label])
                )
                if current:
                    proofs[label] = {
                        key: attempt.get(key)
                        for key in (
                            "attempt_id",
                            "run_id",
                            "report_path",
                            "report_sha256",
                            "start_check",
                        )
                    }
            freshness = "current" if proofs else "changed"
            if len(proofs) == len(scenarios):
                state = "accepted"
            elif not remaining:
                state = "blocked"
            else:
                state = "needs_validation"
        elif not remaining:
            state = "blocked"
        if state == "accepted":
            next_action = "Deliver the current resource files and report the configured checks and evidence scope."
        elif state == "blocked":
            next_action = (
                "Review saved evidence and the task budget before starting further attempts."
            )
        elif latest and latest.get("status") == "pending":
            next_action = "Inspect pending attempt evidence; an interrupted request may already have changed the device."
        else:
            next_action = "Inspect the task and latest evidence, edit resources, then run the task on the intended session."
        return {
            **spec,
            "spec_sha256": spec_sha,
            "state": state,
            "scope": scope,
            "acceptance_scope": "configured_checks_only",
            "constraints_status": "not_automatically_verified"
            if spec.get("constraints")
            else "none",
            "freshness": freshness,
            "attempt_count": len(attempts),
            "remaining_attempts": remaining,
            "latest_attempt": latest,
            "required_scenarios": list(scenarios),
            "completed_scenarios": [label for label in scenarios if label in proofs],
            "missing_scenarios": [label for label in scenarios if label not in proofs],
            "proofs": proofs,
            "task_path": str(self._path(name)),
            "next_action": next_action,
        }

    def run(
        self,
        service: Any,
        session_id: str,
        name: str,
        timeout_seconds: float = 20,
        scenario: str | None = None,
    ) -> dict[str, Any]:
        spec, spec_sha = self._read(name)
        scenarios = self._scenarios(spec)
        if scenario is None:
            if len(scenarios) != 1:
                raise DevError("SCENARIO_REQUIRED", "Choose one of the task's required scenarios")
            scenario = next(iter(scenarios))
        if scenario not in scenarios:
            raise DevError("INVALID_SCENARIO", f"Unknown task scenario: {scenario}")
        if len(self._attempts(name)) >= spec["max_attempts"]:
            raise DevError(
                "TASK_BUDGET_EXHAUSTED", "Task attempt budget exhausted", self.context(name)
            )
        session = service._session(session_id)
        aid = identifier("attempt")
        path = self._attempt_directory(name) / f"{aid}.json"
        record: dict[str, Any] = {
            "schema_version": 1,
            "attempt_id": aid,
            "task": name,
            "spec_sha256": spec_sha,
            "spec": spec,
            "session_id": session_id,
            "resource_path": session.resource_path,
            "scenario": scenario,
            "started_at": now(),
            "status": "pending",
            "accepted": False,
            "scope": "fixture" if session.kind == "demo" else "device",
            "backend": session.kind,
            "device": session.device,
            "inputs": {},
            "case_inputs": {},
            "attempt_path": str(path),
            "acceptance_scope": "configured_checks_only",
            "constraints_status": "not_automatically_verified"
            if spec.get("constraints")
            else "none",
        }
        write_json(path, record)
        try:
            record["case_inputs"] = self._case_snapshot(spec.get("cases"))
            start_node = scenarios[scenario]
            if start_node is not None:
                record["start_inputs"] = self._resource_snapshot(session.resource_path)
                record["start_check"] = self._start_check(service, session_id, start_node)
                if record["start_check"]["hit"] is not True:
                    raise DevError(
                        "START_STATE_MISMATCH",
                        "Current screen does not match the required scenario",
                    )
            result = service.run(
                session_id=session_id,
                entry=spec["entry"],
                goal=spec["goal"],
                timeout_seconds=timeout_seconds,
            )
            record.update(
                {
                    "run_id": result.get("run_id"),
                    "report_path": result.get("report_path"),
                    "inputs": result.get("inputs", {}),
                    "backend": result.get("backend", session.kind),
                    "device": result.get("device", session.device),
                    "scope": result.get("scope", "fixture" if session.kind == "demo" else "device"),
                    "execution_status": result.get("execution_status"),
                    "goal_status": result.get("goal_status"),
                    "after_observation": result.get("after_observation"),
                    "last_recognition": result.get("last_recognition"),
                }
            )
            cases_result = (
                service.case_check(session_id, spec["cases"]) if spec.get("cases") else None
            )
            record["case_check"] = cases_result
            try:
                report_path = self._inside(record["report_path"])
                record["report_sha256"] = _digest(report_path)
            except (TypeError, OSError, DevError) as exc:
                record["report_error"] = str(exc)
            inputs_current = record["inputs"] == self._resource_snapshot(session.resource_path)
            cases_current = record["case_inputs"] == self._case_snapshot(spec.get("cases"))
            spec_current = self._read(name)[1] == spec_sha
            record["accepted"] = (
                result.get("execution_status") == "succeeded"
                and result.get("goal_status") == "passed"
                and result.get("inputs_changed") is False
                and (cases_result is None or cases_result.get("passed") is True)
                and inputs_current
                and cases_current
                and spec_current
                and record["scope"] in {"fixture", "device"}
                and self._report_current(record)
                and self._start_current(record, start_node)
            )
            record["status"] = "completed"
        except Exception as exc:
            record["status"] = "error"
            record["error"] = (
                exc.as_dict()
                if isinstance(exc, DevError)
                else {
                    "code": "TASK_ATTEMPT_FAILED",
                    "message": str(exc),
                    "details": {},
                }
            )
        finally:
            record["finished_at"] = now()
            write_json(path, record)
        return {"attempt": record, "context": self.context(name)}
