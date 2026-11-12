"""Native MaaFramework adapter. Controller coordinates are used without extra resizing."""

from __future__ import annotations

import dataclasses
import math
import queue
import time
from pathlib import Path
from typing import Any

import numpy as np

from vibe_maa.backends.base import EventCallback
from vibe_maa.errors import DevError


def _json(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _json(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _image(value: Any) -> np.ndarray | None:
    if isinstance(value, np.ndarray) and value.size and value.ndim == 3:
        return value.copy()
    return None


def _recognition(detail: Any) -> dict[str, Any]:
    return {
        "reco_id": detail.reco_id,
        "algorithm": str(detail.algorithm),
        "hit": bool(detail.hit),
        "box": _json(detail.box),
        "all_results": _json(detail.all_results),
        "filtered_results": _json(detail.filtered_results),
        "best_result": _json(detail.best_result),
        "raw_detail": _json(detail.raw_detail),
    }


class MaaBackend:
    """One serialized native controller, with fresh resources for each experiment.

    ``controller`` is an injection point for native DbgController integration tests.
    Normal sessions create an ADB controller from ``device`` and ``adb_path``.
    """

    def __init__(
        self,
        project: Path,
        device: str,
        adb_path: str = "adb",
        resource_path: str = "resource",
        *,
        controller: Any = None,
        target_short_side: int = 720,
    ):
        try:
            from maa.controller import AdbController
            from maa.define import LoggingLevelEnum
            from maa.tasker import Tasker
        except (ImportError, OSError, RuntimeError) as exc:
            raise DevError("MAAFW_UNAVAILABLE", f"Cannot load MaaFramework: {exc}") from exc
        self.project = Path(project).resolve()
        self.resource_path = (self.project / resource_path).resolve()
        if not self.resource_path.is_relative_to(self.project):
            raise DevError("INVALID_RESOURCE", "Resource path must be inside the project")
        if not self.resource_path.is_dir():
            raise DevError("INVALID_RESOURCE", f"Missing resource directory: {self.resource_path}")
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        Tasker.set_debug_mode(True)
        log_dir = self.project / ".maa-dev" / "native-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        Tasker.set_log_dir(log_dir)
        self.controller = controller or AdbController(adb_path, device)
        self.tasker: Any = None
        self.resource: Any = None
        self._unusable = False
        if target_short_side <= 0:
            raise DevError("INVALID_ARGUMENT", "target_short_side must be positive")
        if not self.controller.set_screenshot_target_short_side(target_short_side):
            raise DevError("DEVICE_ERROR", "Cannot set controller screenshot coordinate size")
        self._wait_controller(self.controller.post_connection(), 30)

    def _check(self) -> None:
        if self._unusable:
            raise DevError("SESSION_UNUSABLE", "Native operation timed out; open a new session")

    def _wait_controller(self, job: Any, seconds: float = 15) -> Any:
        deadline = time.monotonic() + seconds
        while not job.done:
            if time.monotonic() >= deadline:
                self._unusable = True
                raise DevError("DEVICE_TIMEOUT", "Controller did not finish before its deadline")
            time.sleep(0.01)
        if not job.succeeded:
            raise DevError("DEVICE_ERROR", "Controller operation failed")
        return job

    def _prepare(self, override: dict[str, Any] | None = None) -> Any:
        from maa.resource import Resource
        from maa.tasker import Tasker

        self._check()
        resource = Resource()
        job = resource.post_bundle(self.resource_path)
        deadline = time.monotonic() + 30
        while not job.done:
            if time.monotonic() >= deadline:
                self._unusable = True
                raise DevError("RESOURCE_TIMEOUT", "Resource loading exceeded 30 seconds")
            time.sleep(0.01)
        if not job.succeeded:
            raise DevError("RESOURCE_ERROR", "MaaFramework could not load the project resource")
        if override and not resource.override_pipeline(override):
            raise DevError("INVALID_PIPELINE", "MaaFramework rejected the node definition")
        tasker = Tasker()
        if not tasker.bind(resource, self.controller) or not tasker.inited:
            raise DevError("DEVICE_ERROR", "Cannot bind MaaFramework tasker")
        self.tasker, self.resource = tasker, resource
        return tasker

    def capture(self) -> np.ndarray:
        self._check()
        job = self._wait_controller(self.controller.post_screencap())
        image = _image(job.get())
        if image is None:
            raise DevError("CAPTURE_FAILED", "Controller returned an empty image")
        return image

    def act(self, action: dict[str, Any]) -> bool:
        self._check()
        kind = action.get("type")
        if kind == "click":
            job = self.controller.post_click(action["x"], action["y"])
        elif kind == "swipe":
            job = self.controller.post_swipe(
                action["x1"], action["y1"], action["x2"], action["y2"], action["duration"]
            )
        elif kind == "key":
            job = self.controller.post_click_key(action["key"])
        elif kind == "text":
            job = self.controller.post_input_text(action["text"])
        else:
            raise DevError("INVALID_ACTION", f"Unsupported action type: {kind}")
        return bool(self._wait_controller(job).succeeded)

    def ocr(self, image: np.ndarray) -> list[dict[str, Any]]:
        if not (self.resource_path / "model" / "ocr").is_dir():
            raise DevError("OCR_UNAVAILABLE", "Install OCR models in resource/model/ocr")
        result = self.recognize(image, {"recognition": "OCR", "expected": []})
        return [
            {"text": item.get("text", ""), "box": item["box"], "score": item.get("score")}
            for item in result["all_results"]
            if item.get("box")
        ]

    def recognize(self, image: np.ndarray, node: dict[str, Any]) -> dict[str, Any]:
        """Recognize supplied pixels; never execute the node's action or next list."""
        if node.get("enabled") is False:
            raise DevError("UNSUPPORTED_PROBE", "Disabled nodes do not have a probe result")
        probe_node = {**node, "action": "DoNothing", "next": [], "on_error": []}
        tasker = self._prepare({"__maa_dev_probe": probe_node})
        normalized = self.resource.get_node_data("__maa_dev_probe")
        if not normalized:
            raise DevError("INVALID_PIPELINE", "Cannot resolve probe node")
        if normalized.get("enabled") is False:
            raise DevError("UNSUPPORTED_PROBE", "Disabled nodes do not have a probe result")
        recognition = normalized["recognition"]
        kind, params = recognition["type"], recognition["param"]
        if kind in {"Custom", "And", "Or"} or isinstance(params.get("roi"), (str, bool)):
            raise DevError(
                "UNSUPPORTED_PROBE",
                "v0.1 probes support built-in recognition with numeric ROI; "
                "Custom, And/Or and context references need a live task context",
            )
        parsed = self.resource.get_node_object("__maa_dev_probe")
        if parsed is None:
            raise DevError("INVALID_PIPELINE", "Cannot parse native recognition parameters")
        rec = parsed.recognition
        job = tasker.post_recognition(rec.type, rec.param, image)
        timed_out = self._wait_task(tasker, job, 20)
        if timed_out:
            raise DevError("RECOGNITION_TIMEOUT", "Recognition exceeded 20 seconds")
        detail = job.get()
        if detail is None or not detail.nodes or not detail.nodes[0].recognition:
            raise DevError("RECOGNITION_FAILED", "MaaFramework returned no recognition detail")
        result = _recognition(detail.nodes[0].recognition)
        if normalized.get("inverse"):
            result["recognition_hit"] = result["hit"]
            result["hit"] = not result["hit"]
        return result

    def _wait_task(self, tasker: Any, job: Any, seconds: float, drain: Any = lambda: None) -> bool:
        deadline = time.monotonic() + seconds
        while not job.done:
            drain()
            if time.monotonic() >= deadline:
                stop = tasker.post_stop()
                stop_deadline = time.monotonic() + 3
                while not stop.done and time.monotonic() < stop_deadline:
                    drain()
                    time.sleep(0.01)
                drain()
                if not stop.done or not job.done:
                    self._unusable = True
                    raise DevError(
                        "STOP_TIMEOUT", "Native task did not stop; session cannot be reused"
                    )
                return True
            time.sleep(0.01)
        drain()
        return False

    def run(
        self,
        entry: str,
        override: dict[str, Any],
        timeout_seconds: float,
        on_event: EventCallback,
    ) -> dict[str, Any]:
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise DevError("INVALID_ARGUMENT", "timeout_seconds must be finite and positive")
        from maa.context import ContextEventSink

        tasker = self._prepare()
        events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4096)
        dropped = 0

        class Sink(ContextEventSink):
            def on_raw_notification(self, context: Any, msg: str, details: dict[str, Any]) -> None:
                nonlocal dropped
                try:
                    events.put_nowait(
                        {
                            "message": msg,
                            "details": _json(details),
                            "received_at_ns": time.time_ns(),
                        }
                    )
                except queue.Full:
                    dropped += 1

        def drain() -> None:
            # A busy native producer must not keep us here past the task deadline.
            for _ in range(128):
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    return
                image = None
                if event["message"] in {"Node.Recognition.Succeeded", "Node.Recognition.Failed"}:
                    reco_id = event["details"].get("reco_id")
                    detail = tasker.get_recognition_detail(reco_id) if reco_id else None
                    if detail is not None:
                        event["recognition"] = _recognition(detail)
                        image = _image(detail.raw_image)
                    if image is None:
                        event["missing_evidence"] = "Recognition image unavailable or evicted"
                if event["message"] in {"Node.Action.Succeeded", "Node.Action.Failed"}:
                    action_id = event["details"].get("action_id")
                    action = tasker.get_action_detail(action_id) if action_id else None
                    if action is not None:
                        event["action"] = _json(action)
                on_event(event, image)

        sink_id = tasker.add_context_sink(Sink())
        if sink_id is None:
            raise DevError("EVENT_ERROR", "Could not attach native event recorder")
        try:
            job = tasker.post_task(entry, override)
            timed_out = self._wait_task(tasker, job, timeout_seconds, drain)
            while not events.empty():
                drain()
            detail = job.get()
            nodes = []
            node_ids = detail.node_id_list if detail is not None else []
            for node_id in node_ids[-256:]:
                nd = tasker.get_node_detail(node_id)
                if nd is not None:
                    record = {"name": nd.name, "node_id": nd.node_id, "completed": nd.completed}
                    if nd.recognition:
                        record["recognition"] = _recognition(nd.recognition)
                    if nd.action:
                        record["action"] = _json(nd.action)
                    nodes.append(record)
            if dropped:
                on_event({"message": "Evidence.Truncated", "dropped_events": dropped}, None)
            if timed_out:
                status = "timeout"
            elif detail is not None and detail.status.succeeded:
                status = "succeeded"
            else:
                status = "failed"
            return {
                "execution_status": status,
                "nodes": nodes,
                "task_id": job.job_id,
                "omitted_nodes": max(0, len(node_ids) - 256),
            }
        except BaseException:
            if tasker.running:
                stop = tasker.post_stop()
                deadline = time.monotonic() + 3
                while not stop.done and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not stop.done:
                    self._unusable = True
            raise
        finally:
            tasker.remove_context_sink(sink_id)

    def close(self) -> None:
        if self.tasker is not None and self.tasker.running:
            stop = self.tasker.post_stop()
            deadline = time.monotonic() + 3
            while not stop.done and time.monotonic() < deadline:
                time.sleep(0.01)
            if not stop.done:
                self._unusable = True
                raise DevError("STOP_TIMEOUT", "Native task has not stopped")
        self._unusable = True
