"""Independent acceptance checks. This module is outside the agent's writable project."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from browser_support import browser_oracle
from drawio_target import TARGET as DRAWIO
from jupyterlab_target import TARGET as JUPYTERLAB
from pdfjs_target import TARGET as PDFJS

from vibe_maa.errors import DevError
from vibe_maa.evidence import read_image
from vibe_maa.service import DevelopmentService, load_nodes

BROWSER_TARGETS = {"drawio": DRAWIO, "jupyterlab": JUPYTERLAB, "pdfjs": PDFJS}
TARGETS = (*BROWSER_TARGETS, "mailbox")
BROWSER_WIDTH, BROWSER_HEIGHT = 1280, 720
ENTRY = "AgentTask"
ALLOWED_ACTIONS = {
    "DoNothing",
    "Click",
    "LongPress",
    "Swipe",
    "ClickKey",
    "KeyDown",
    "KeyUp",
    "InputText",
}


def validate_submission(project: Path) -> None:
    if any((project / "resource").rglob("default_pipeline.json")):
        raise DevError(
            "INVALID_SUBMISSION",
            "This benchmark requires explicit nodes; default_pipeline.json is not allowed",
        )
    nodes = load_nodes(project)
    if ENTRY not in nodes:
        raise DevError("INVALID_SUBMISSION", f"Deliver a standard Pipeline with entry {ENTRY}")
    for name, node in nodes.items():
        action = node.get("action", "DoNothing")
        kind = action.get("type") if isinstance(action, dict) else action
        if kind not in ALLOWED_ACTIONS:
            raise DevError("INVALID_SUBMISSION", f"Disallowed acceptance action in {name}: {kind}")
        recognition = node.get("recognition", "DirectHit")
        kind = recognition.get("type") if isinstance(recognition, dict) else recognition
        if kind == "Custom":
            raise DevError("INVALID_SUBMISSION", "Custom handlers are outside this benchmark")


def grade_mailbox(project: Path) -> dict[str, Any]:
    validate_submission(project)
    checks = []
    for state in ["home", "mailbox", "popup"]:
        service = DevelopmentService(project)
        try:
            opened = service.session_open(backend="demo", initial_state=state)
            report = service.run(opened["session_id"], ENTRY, timeout_seconds=20)
            backend = service.sessions[opened["session_id"]].backend
            paths = [e["image_path"] for e in report["events"] if e.get("image_path")]
            visited_mailbox = any(
                np.array_equal(read_image(Path(p))[0, 0], [40] * 3) for p in paths
            )
            final_home = backend.world.state == "home"
            passed = report["execution_status"] == "succeeded" and final_home and visited_mailbox
            checks.append(
                {
                    "starting_state": state,
                    "execution_status": report["execution_status"],
                    "visited_mailbox": visited_mailbox,
                    "returned_home": final_home,
                    "passed": passed,
                    "run_id": report["run_id"],
                }
            )
        finally:
            service.close()
    return {
        "passed": all(c["passed"] for c in checks),
        "target": "mailbox",
        "scope": "fixture",
        "checks": checks,
        "oracle": "fresh native fixture state plus original recognition frames",
    }


def launch_target(name: str, cache: Path):
    if name == "mailbox":
        return contextlib.nullcontext((None, None))
    target = BROWSER_TARGETS[name]
    return target.launch(cache, target.variants[0])


def grade_target(
    project: Path, name: str, cache: Path, browser_executable: str | None = None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if name == "mailbox":
        return grade_mailbox(project), None
    target = BROWSER_TARGETS[name]
    validate_submission(project)
    checks = []
    blocked_requests = []
    upstream = None
    for repetition, variant in enumerate(target.variants, start=1):
        check = {"repetition": repetition, "starting_state": variant, "passed": False}
        blocked = []
        try:
            with (
                target.launch(cache, variant) as (url, metadata),
                browser_oracle(url, target) as blocked,
                contextlib.closing(DevelopmentService(project)) as service,
            ):
                if upstream is None:
                    upstream = metadata
                options = {
                    "backend": "browser",
                    "url": url,
                    "width": BROWSER_WIDTH,
                    "height": BROWSER_HEIGHT,
                }
                if browser_executable:
                    options["browser_executable"] = browser_executable
                opened = service.session_open(**options)
                session_id = opened["session_id"]
                worker = service.sessions[session_id].backend._worker
                worker.call("__grader_ready")
                before = worker.call("__grader_snapshot")
                check["before"] = before
                report = service.run(session_id, ENTRY, timeout_seconds=30)
                check.update(run_id=report["run_id"], execution_status=report["execution_status"])
                after = worker.call("__grader_snapshot")
                completed = target.completed(before, after)
                check.update(after=after, application_goal_met=completed)
            check["passed"] = (
                report["execution_status"] == "succeeded" and completed and not blocked
            )
        except Exception as exc:
            check["error"] = {"type": type(exc).__name__, "message": str(exc)}
        blocked_requests.extend(blocked)
        checks.append(check)
    grade = {
        "passed": all(c["passed"] for c in checks),
        "target": name,
        "scope": "application",
        "checks": checks,
        "blocked_external_requests": blocked_requests,
        "oracle": target.oracle,
    }
    return grade, upstream


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
