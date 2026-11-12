"""Independent acceptance checks. This module is outside the agent's writable project."""

from __future__ import annotations

import contextlib
import functools
import http.server
import importlib.util
import json
import threading
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import numpy as np

from vibe_maa.errors import DevError
from vibe_maa.evidence import read_image
from vibe_maa.service import DevelopmentService, load_nodes

ROOT = Path(__file__).resolve().parents[2]
ENTRY = "AgentTask"
ALLOWED_ACTIONS = {"DoNothing", "Click", "LongPress", "Swipe", "ClickKey", "InputText"}


def validate_submission(project: Path) -> None:
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


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


@contextlib.contextmanager
def drawio_server(cache: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    source = ROOT / "integration/targets/drawio/prepare.py"
    spec = importlib.util.spec_from_file_location("vibe_drawio_prepare", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("draw.io provisioner is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    webroot = module.provision(cache)
    metadata = module.manifest()
    handler = functools.partial(_QuietHandler, directory=str(webroot))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{metadata['launch_path']}", metadata
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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


@contextlib.contextmanager
def _drawio_oracle(url: str) -> Iterator[list[str]]:
    """Test-only private DOM oracle. No DOM operation is added to the product API."""
    from playwright.sync_api import Browser

    from vibe_maa.backends.browser import _BrowserWorker

    original = _BrowserWorker._dispatch
    original_context = Browser.new_context
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    blocked = []

    def context(browser: Any, *args: Any, **kwargs: Any) -> Any:
        kwargs["service_workers"] = "block"
        instance = original_context(browser, *args, **kwargs)
        instance.add_init_script("""
            window.addEventListener('load', () => {
                const init = App.prototype.init;
                App.prototype.init = function() {
                    window.__vibe_grader_editor = this;
                    return init.apply(this, arguments);
                };
            }, {once: true});
        """)

        def route(request: Any) -> None:
            target = urlsplit(request.request.url)
            if f"{target.scheme}://{target.netloc}" == origin or target.scheme in {"data", "blob"}:
                request.continue_()
            else:
                blocked.append(request.request.url)
                request.abort()

        instance.route("**/*", route)
        return instance

    def dispatch(page: Any, operation: str, args: tuple[Any, ...]) -> Any:
        if operation == "__grader_ready":
            page.wait_for_function(
                "window.__vibe_grader_editor && __vibe_grader_editor.getCurrentFile() && __vibe_grader_editor.editor.graph.isEnabled()",
                timeout=10000,
            )
            return True
        if operation == "__grader_document":
            return page.evaluate("""() => {
                const graph = window.__vibe_grader_editor.editor.graph;
                return Object.values(graph.model.cells).filter(cell => cell.vertex).map(cell => ({
                    label: graph.convertValueToString(cell), style: cell.style || '',
                    shape: graph.getCellStyle(cell).shape,
                    width: cell.geometry.width, height: cell.geometry.height
                }));
            }""")
        return original(page, operation, args)

    _BrowserWorker._dispatch = staticmethod(dispatch)
    Browser.new_context = context
    try:
        yield blocked
    finally:
        _BrowserWorker._dispatch = staticmethod(original)
        Browser.new_context = original_context


def grade_drawio(project: Path, url: str, browser_executable: str | None = None) -> dict[str, Any]:
    validate_submission(project)
    checks = []
    with _drawio_oracle(url) as blocked:
        for repetition in range(2):
            service = DevelopmentService(project)
            try:
                options = {"backend": "browser", "url": url}
                if browser_executable:
                    options["browser_executable"] = browser_executable
                opened = service.session_open(**options)
                backend = service.sessions[opened["session_id"]].backend
                backend._worker.call("__grader_ready")
                before = backend._worker.call("__grader_document")
                report = service.run(opened["session_id"], ENTRY, timeout_seconds=30)
                document = backend._worker.call("__grader_document")
                changed = (
                    not before
                    and len(document) == 1
                    and document[0]["label"] == "Vibe Maa"
                    and document[0]["shape"] == "rectangle"
                    and document[0]["width"] > 0
                    and document[0]["height"] > 0
                )
                checks.append(
                    {
                        "repetition": repetition + 1,
                        "execution_status": report["execution_status"],
                        "label_in_canvas": changed,
                        "document": document,
                        "run_id": report["run_id"],
                        "passed": report["execution_status"] == "succeeded" and changed,
                    }
                )
            finally:
                service.close()
    return {
        "passed": all(c["passed"] for c in checks) and not blocked,
        "target": "drawio",
        "scope": "application",
        "checks": checks,
        "blocked_external_requests": blocked,
        "oracle": "fresh real application canvas DOM, independent from agent-supplied goals",
    }


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
