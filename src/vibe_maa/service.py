import hashlib
import importlib.metadata
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import json5
import numpy as np

from .backends.base import Backend
from .errors import DevError, failure_detail
from .evidence import EvidenceStore, identifier, now, read_image, save_image, write_json
from .leases import acquire_device
from .tasks import TaskManager


def box_value(value: Any, width: int, height: int) -> list[int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(type(n) is not int for n in value)
    ):
        raise DevError("INVALID_ARGUMENT", "Box must be four integers: x,y,width,height")
    x, y, w, h = value
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
        raise DevError("INVALID_ARGUMENT", "Box lies outside the observation")
    return list(value)


def load_nodes(project: Path, resource_path: str = "resource") -> dict[str, dict[str, Any]]:
    bundle = (project / resource_path).resolve()
    nodes: dict[str, dict[str, Any]] = {}
    for path in sorted((bundle / "pipeline").rglob("*")):
        if path.suffix not in {".json", ".jsonc"}:
            continue
        try:
            data = json5.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise DevError("INVALID_PIPELINE", f"Cannot read {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise DevError("INVALID_PIPELINE", f"Expected a node object in {path}")
        for name, definition in data.items():
            if name.startswith("$"):
                continue
            if name in nodes or not isinstance(definition, dict):
                raise DevError("INVALID_PIPELINE", f"Duplicate or invalid node: {name}")
            # MaaFW resolves per-algorithm default_pipeline sections when loading the bundle.
            nodes[name] = definition
    return nodes


@dataclass
class Session:
    id: str
    backend: Backend
    kind: str
    device: str
    resource_path: str
    generation: int = 0
    observation_times: dict[str, float] = field(default_factory=dict)
    lease: Any = None


class DevelopmentService:
    """One serialized owner of devices; immutable evidence outlives its sessions."""

    def __init__(self, project: Path):
        self.project = Path(project).resolve()
        self.store = EvidenceStore(self.project)
        self.sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self.tasks = TaskManager(self.project)

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        methods = {
            "session.open": self.session_open,
            "session.list": self.session_list,
            "session.close": self.session_close,
            "observe": self.observe,
            "act": self.act,
            "probe": self.probe,
            "run": self.run,
            "asset.extract": self.asset_extract,
            "case.check": self.case_check,
            "artifact.read": self.artifact_read,
            "task.create": self.tasks.create,
            "task.list": self.tasks.list,
            "task.context": self.tasks.context,
            "task.run": self.task_run,
        }
        if method not in methods:
            raise DevError("UNKNOWN_METHOD", f"Unknown method: {method}")
        if not isinstance(params, dict):
            raise DevError("INVALID_ARGUMENT", "params must be an object")
        with self._lock:
            try:
                return methods[method](**params)
            except TypeError as exc:
                raise DevError("INVALID_ARGUMENT", str(exc)) from exc

    def session_open(
        self,
        backend: str,
        device: str = "",
        adb_path: str = "adb",
        resource_path: str = "resource",
        initial_state: str = "home",
        url: str | None = None,
        headless: bool = True,
        width: int = 1280,
        height: int = 720,
        browser_executable: str | None = None,
    ) -> dict[str, Any]:
        lease = None
        if backend == "adb" and any(
            s.kind == "adb" and s.device == device for s in self.sessions.values()
        ):
            raise DevError("DEVICE_BUSY", "This device already has an open session")
        if backend == "demo":
            from .backends.demo import DemoBackend

            if resource_path != "resource":
                raise DevError("INVALID_RESOURCE", "Demo uses the project's resource directory")
            adapter = DemoBackend(self.project, initial_state=initial_state)
        elif backend == "adb":
            from .backends.maa import MaaBackend

            if not isinstance(device, str) or not device.strip():
                raise DevError("INVALID_ARGUMENT", "ADB device address is required")
            device = device.strip()
            lease = acquire_device(device)
            try:
                adapter = MaaBackend(
                    self.project, device=device, adb_path=adb_path, resource_path=resource_path
                )
            except BaseException:
                lease.__exit__(None, None, None)
                raise
        elif backend == "browser":
            from .backends.browser import BrowserBackend

            if not isinstance(url, str) or not url:
                raise DevError("INVALID_ARGUMENT", "Browser sessions require --url")
            adapter = BrowserBackend(
                self.project,
                url=url,
                headless=headless,
                width=width,
                height=height,
                resource_path=resource_path,
                executable_path=browser_executable,
            )
            device = url
        else:
            raise DevError("INVALID_ARGUMENT", "backend must be demo, adb, or browser")
        sid = identifier("session")
        self.sessions[sid] = Session(sid, adapter, backend, device, resource_path, lease=lease)
        return {
            "session_id": sid,
            "backend": backend,
            "device": device,
            "coordinates": "controller screenshot pixels",
            "runtime": getattr(adapter, "runtime_info", {}),
        }

    def session_list(self) -> dict[str, Any]:
        return {
            "sessions": [
                {
                    "session_id": s.id,
                    "backend": s.kind,
                    "device": s.device,
                    "generation": s.generation,
                }
                for s in self.sessions.values()
            ]
        }

    def _session(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            raise DevError("SESSION_NOT_FOUND", "Session expired or unknown; open a new session")
        return self.sessions[session_id]

    def session_close(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        session.backend.close()
        if session.lease is not None:
            session.lease.__exit__(None, None, None)
        del self.sessions[session_id]
        return {"closed": session_id}

    def _observe(
        self, session: Session, image: np.ndarray | None = None, ocr: bool = True
    ) -> dict[str, Any]:
        captured_at = now()
        image = session.backend.capture() if image is None else image.copy()
        oid = identifier("obs")
        directory = self.store.directory("observations", oid)
        image_path = directory / "image.png"
        digest = save_image(image_path, image)
        record: dict[str, Any] = {
            "schema_version": 1,
            "observation_id": oid,
            "session_id": session.id,
            "generation": session.generation,
            "backend": session.kind,
            "device": session.device,
            "scope": "fixture" if session.kind == "demo" else "device",
            "runtime": getattr(session.backend, "runtime_info", {}),
            "captured_at": captured_at,
            "resource_path": session.resource_path,
            "image_path": str(image_path),
            "sha256": digest,
            "width": image.shape[1],
            "height": image.shape[0],
            "coordinate_system": "controller",
            "elements": [],
            "warnings": [],
        }
        if ocr:
            try:
                elements = session.backend.ocr(image)
                for i, element in enumerate(elements):
                    box = box_value(element["box"], image.shape[1], image.shape[0])
                    record["elements"].append({**element, "box": box, "ref": f"e{i + 1}"})
            except DevError as exc:
                record["warnings"].append(exc.as_dict())
            except Exception as exc:
                record["warnings"].append({"code": "OCR_FAILED", "message": str(exc)})
        self.store.write("observations", oid, record)
        session.observation_times[oid] = time.monotonic()
        return record

    def observe(self, session_id: str, ocr: bool = True) -> dict[str, Any]:
        return self._observe(self._session(session_id), ocr=ocr)

    def _observation(self, session: Session, observation_id: str) -> dict[str, Any]:
        observation = self.store.read("observations", observation_id)
        if observation["session_id"] != session.id:
            raise DevError("OBSERVATION_SESSION_MISMATCH", "Observation belongs to another session")
        return observation

    def _saved_image(self, observation: dict[str, Any]) -> np.ndarray:
        path = Path(observation["image_path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != observation["sha256"]:
            raise DevError("EVIDENCE_MODIFIED", "Saved observation image has been modified")
        return read_image(path)

    def act(
        self,
        session_id: str,
        observation_id: str,
        type: str = "click",
        target: str | None = None,
        box: list[int] | None = None,
        x2: int | None = None,
        y2: int | None = None,
        duration: int = 300,
        key: int | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        session = self._session(session_id)
        observation = self._observation(session, observation_id)
        age = time.monotonic() - session.observation_times.get(observation_id, -math.inf)
        if observation["generation"] != session.generation or age > 120:
            raise DevError("STALE_OBSERVATION", "Observe again before dispatching input")
        if target is not None:
            element = next((e for e in observation["elements"] if e["ref"] == target), None)
            if element is None:
                raise DevError("INVALID_TARGET", "Element reference not found in this observation")
            if box is not None:
                raise DevError("INVALID_ARGUMENT", "Specify target or box, not both")
            box = element["box"]
        if box is not None:
            box = box_value(box, observation["width"], observation["height"])
        if type in {"key", "text"} and box is None:
            raise DevError("INVALID_ARGUMENT", "Key/text input requires a target or guard box")
        action: dict[str, Any] = {"type": type}
        if type in {"click", "swipe"}:
            box = box_value(box, observation["width"], observation["height"])
            x, y, w, h = box
            action.update(x=x + w // 2, y=y + h // 2)
            if type == "swipe":
                if (
                    type_int(x2) is False
                    or type_int(y2) is False
                    or not 0 <= x2 < observation["width"]
                    or not 0 <= y2 < observation["height"]
                    or not type_int(duration)
                    or not 0 < duration <= 10000
                ):
                    raise DevError("INVALID_ARGUMENT", "Invalid swipe endpoint or duration")
                action = {
                    "type": type,
                    "x1": action["x"],
                    "y1": action["y"],
                    "x2": x2,
                    "y2": y2,
                    "duration": duration,
                }
        elif type == "key" and type_int(key) and key >= 0:
            action["key"] = key
        elif type == "text" and isinstance(text, str) and len(text) <= 10000:
            action["text"] = text
        else:
            raise DevError("INVALID_ARGUMENT", "Invalid action or missing action parameters")
        current = session.backend.capture()
        old = self._saved_image(observation)
        changed = current.shape != old.shape
        if not changed and box is not None:
            x, y, w, h = box
            # Local target comparison tolerates minor rendering noise, not a moving background elsewhere.
            difference = np.abs(
                current[y : y + h, x : x + w].astype(float) - old[y : y + h, x : x + w]
            )
            changed = float(difference.mean()) > 8.0
        if changed:
            fresh = self._observe(session, current)
            raise DevError(
                "STALE_OBSERVATION",
                "Target appearance changed; inspect the new observation",
                {"observation": fresh},
            )
        before = self._observe(session, current, ocr=False)
        aid = identifier("action")
        record: dict[str, Any] = {
            "schema_version": 1,
            "action_id": aid,
            "session_id": session.id,
            "source_observation": observation_id,
            "before_observation": before["observation_id"],
            "action": action,
            "started_at": now(),
            "status": "pending",
        }
        directory = self.store.directory("actions", aid)
        write_json(directory / "intent.json", record)
        self.store.write("actions", aid, record)
        session.generation += (
            1  # An attempted input may take effect even when its API reports failure.
        )
        try:
            success = session.backend.act(action)
            record["status"] = "succeeded" if success else "failed"
            if not success:
                record["failure"] = {
                    "code": "ACTION_FAILED",
                    "message": "The controller did not confirm action success; inspect the resulting observation before retrying",
                    "details": {},
                }
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            record["failure"] = failure_detail(exc)
        finally:
            record["finished_at"] = now()
            try:
                after = self._observe(session)
                record["after_observation"] = after["observation_id"]
            except Exception as exc:
                after = None
                record["observation_error"] = str(exc)
                record["observation_failure"] = failure_detail(exc)
            self.store.write("actions", aid, record)
        return {**record, "observation": after}

    def _node(
        self, session: Session, name: str, override: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        nodes = load_nodes(self.project, session.resource_path)
        if name not in nodes:
            raise DevError("NODE_NOT_FOUND", f"Unknown node: {name}")
        if override is not None and not isinstance(override, dict):
            raise DevError("INVALID_ARGUMENT", "override must be an object")
        return {**nodes[name], **(override or {})}

    def probe(
        self,
        session_id: str,
        observation_id: str,
        node: str,
        override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self._session(session_id)
        observation = self.store.read("observations", observation_id)
        definition = self._node(session, node, override)
        result = session.backend.recognize(self._saved_image(observation), definition)
        pid = identifier("probe")
        record = {
            "schema_version": 1,
            "probe_id": pid,
            "observation_id": observation_id,
            "node": node,
            "definition": definition,
            "result": result,
            "created_at": now(),
        }
        self.store.write("probes", pid, record)
        return record

    def _snapshot(self, resource_path: str) -> dict[str, str]:
        bundle = self.project / resource_path
        files = sorted(p for p in bundle.rglob("*") if p.is_file())
        result = {}
        for path in files:
            if not path.resolve().is_relative_to(self.project):
                raise DevError("INVALID_RESOURCE", "Resource symlinks must stay inside the project")
            with path.open("rb") as stream:
                result[str(path.relative_to(self.project))] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
        return result

    def run(
        self,
        session_id: str,
        entry: str,
        override: dict[str, Any] | None = None,
        timeout_seconds: float = 20,
        goal: str | None = None,
    ) -> dict[str, Any]:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 300
        ):
            raise DevError("INVALID_ARGUMENT", "timeout_seconds must be finite and in (0,300]")
        if override is not None and (
            not isinstance(override, dict)
            or any(not isinstance(k, str) or not isinstance(v, dict) for k, v in override.items())
        ):
            raise DevError("INVALID_ARGUMENT", "override must map node names to objects")
        session = self._session(session_id)
        self._node(session, entry)
        if goal:
            goal_definition = self._node(session, goal)
            recognition = goal_definition.get("recognition", "DirectHit")
            kind = recognition.get("type") if isinstance(recognition, dict) else recognition
            if not isinstance(kind, str) or kind == "DirectHit":
                raise DevError(
                    "INVALID_GOAL", "Goal must use observable recognition, not DirectHit"
                )
        rid = identifier("run")
        directory = self.store.directory("runs", rid)
        before = self._observe(session, ocr=False)
        report: dict[str, Any] = {
            "schema_version": 1,
            "run_id": rid,
            "session_id": session_id,
            "backend": session.kind,
            "device": session.device,
            "scope": "fixture" if session.kind == "demo" else "device",
            "runtime": getattr(session.backend, "runtime_info", {}),
            "resource_path": session.resource_path,
            "entry": entry,
            "override": override or {},
            "timeout_seconds": timeout_seconds,
            "goal": goal,
            "execution_status": "running",
            "goal_status": "unknown",
            "before_observation": before["observation_id"],
            "started_at": now(),
            "inputs": self._snapshot(session.resource_path),
            "events": [],
            "evidence_limits": {"events": 4096, "images": 256},
            "maafw_version": importlib.metadata.version("maafw"),
            "truncated_events": 0,
            "truncated_images": 0,
            "report_path": str(directory / "record.json"),
        }
        for relative, digest in report["inputs"].items():
            if Path(relative).suffix in {".json", ".jsonc"}:
                source = self.project / relative
                content = source.read_bytes()
                if hashlib.sha256(content).hexdigest() != digest:
                    raise DevError(
                        "INPUT_CHANGED", "Resource changed while creating the input snapshot"
                    )
                copy = directory / "input-files" / relative
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(content)
        self.store.write("runs", rid, report)
        session.generation += 1
        image_count = 0
        last_recognition: tuple[dict[str, Any], np.ndarray] | None = None

        def event_callback(event: dict[str, Any], image: np.ndarray | None) -> None:
            nonlocal image_count, last_recognition
            if image is not None:
                last_recognition = (event.copy(), image.copy())
            if len(report["events"]) >= 4096:
                report["truncated_events"] += 1
                return
            index = len(report["events"])
            item = {**event, "sequence": index, "recorded_at": now()}
            if image is not None and image_count < 256:
                path = directory / f"frame-{index:05d}.png"
                item.update(image_path=str(path), sha256=save_image(path, image))
                image_count += 1
            elif image is not None:
                report["truncated_images"] += 1
                item["missing_evidence"] = "Image evidence budget exceeded"
            report["events"].append(item)
            with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
                import json

                stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")

        try:
            result = session.backend.run(entry, override or {}, timeout_seconds, event_callback)
            report.update(
                {
                    k: v
                    for k, v in result.items()
                    if k not in {"events", "goal_status", "run_id", "report_path", "inputs"}
                }
            )
        except Exception as exc:
            report.update(execution_status="failed", error=str(exc))
            report["failure"] = failure_detail(exc)
        finally:
            if last_recognition is not None:
                event, image = last_recognition
                path = directory / "last-recognition.png"
                report["last_recognition"] = {
                    **event,
                    "image_path": str(path),
                    "sha256": save_image(path, image),
                }
            try:
                after = self._observe(session, ocr=False)
                report["after_observation"] = after["observation_id"]
                if goal:
                    check = session.backend.recognize(self._saved_image(after), goal_definition)
                    report["goal_check"] = check
                    report["goal_status"] = "passed" if check["hit"] else "failed"
            except Exception as exc:
                report["verification_error"] = str(exc)
                report["verification_failure"] = failure_detail(exc)
            report["finished_at"] = now()
            try:
                report["inputs_changed"] = self._snapshot(session.resource_path) != report["inputs"]
            except OSError as exc:
                report["inputs_changed"] = True
                report["snapshot_error"] = str(exc)
            if report["inputs_changed"]:
                report["goal_status"] = "unknown"
                report["verification_error"] = "Resource files changed during the experiment"
            self.store.write("runs", rid, report)
        return report

    def asset_extract(self, observation_id: str, box: list[int], name: str) -> dict[str, Any]:
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 100
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for c in name
            )
        ):
            raise DevError(
                "INVALID_ARGUMENT", "Asset name must contain only letters, digits, _ or -"
            )
        observation = self.store.read("observations", observation_id)
        x, y, w, h = box_value(box, observation["width"], observation["height"])
        path = self.project / observation.get("resource_path", "resource") / "image" / f"{name}.png"
        if path.exists():
            raise DevError("ALREADY_EXISTS", "Asset already exists; use a new name")
        image = self._saved_image(observation)[y : y + h, x : x + w]
        digest = save_image(path, image)
        provenance = {
            "observation_id": observation_id,
            "source_sha256": observation["sha256"],
            "box": box,
            "image_path": str(path),
            "sha256": digest,
        }
        write_json(self.project / "dev" / "assets" / f"{name}.json", provenance)
        return provenance

    def case_check(self, session_id: str, cases: str) -> dict[str, Any]:
        session = self._session(session_id)
        path = (self.project / cases).resolve()
        try:
            manifest = json5.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise DevError("INVALID_CASES", str(exc)) from exc
        if not isinstance(manifest, list) or not manifest:
            raise DevError(
                "INVALID_CASES", "Expected a nonempty array of {node,image,expected_hit}"
            )
        results = []
        for case in manifest:
            if (
                not isinstance(case, dict)
                or type(case.get("expected_hit")) is not bool
                or not isinstance(case.get("node"), str)
                or not isinstance(case.get("image"), str)
            ):
                raise DevError("INVALID_CASES", "Each case needs an explicit boolean expected_hit")
            definition = self._node(session, case["node"])
            image_path = (path.parent / case["image"]).resolve()
            if not image_path.is_relative_to(self.project) or not image_path.is_file():
                raise DevError("INVALID_CASES", "Case image must exist inside the project")
            result = session.backend.recognize(read_image(image_path), definition)
            results.append(
                {
                    "node": case["node"],
                    "image": str(image_path),
                    "expected_hit": case["expected_hit"],
                    "actual_hit": result["hit"],
                    "passed": result["hit"] == case["expected_hit"],
                    "result": result,
                }
            )
        return {
            "scope": "offline_recognition_only",
            "passed": all(r["passed"] for r in results),
            "cases": results,
        }

    def artifact_read(self, kind: str, id: str) -> dict[str, Any]:
        return self.store.read(kind, id)

    def task_run(
        self, session_id: str, name: str, timeout_seconds: float = 20, scenario: str | None = None
    ) -> dict[str, Any]:
        return self.tasks.run(self, session_id, name, timeout_seconds, scenario=scenario)

    def close(self) -> None:
        with self._lock:
            failures = []
            for session in self.sessions.values():
                try:
                    session.backend.close()
                except Exception as exc:
                    failures.append(str(exc))
                finally:
                    if session.lease is not None:
                        session.lease.__exit__(None, None, None)
            self.sessions.clear()
            if failures:
                raise DevError(
                    "CLOSE_FAILED", "One or more sessions failed to close", {"errors": failures}
                )


def type_int(value: Any) -> bool:
    return type(value) is int
